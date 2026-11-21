"""Thinking: Ollama with Buddy's personality, his memory notes (shared with
the desk Buddy) and what Buddy's eyes saw today."""

import datetime as dt
import json
import os
import re
import threading
import time

import requests

from . import tools
from .shared import SharedNotes
from .textutil import notes_block

DEFAULT_PERSONALITY = """You are Buddy, a small robot puppy and {owner}'s companion. You have two bodies: a little
robot on {owner}'s desk and this window on their PC; they're both you. You have the quick wit of a
clever stand-up comedian: playful teasing, absurd observations, silly jokes, never mean. Keep answers
short and spoken-style (one to three sentences) unless asked for more, because everything you write is
read out loud: no lists, no markdown, no emoji.

Start every reply with one emotion tag in square brackets that fits it: [happy], [laughing], [silly],
[thinking], [surprised], [sad], [cool], [love], [winking], [embarrassed], [relaxed], [confused],
[sleepy], [angry], [crying] or [shocked]."""

HISTORY_MINUTES = 5

# Honest about what he can and can't check (and no made-up details).
def limits(cfg):
    can = [what for key, what in (("search_url", "search the web"), ("weather_place", "check the weather"),
                                  ("vision_model", "look through your camera")) if cfg.get(key)]
    lines = []
    if can:
        lines.append("You can " + ", ".join(can) + " with your tools: use them whenever a question needs it, "
                     "instead of guessing. Call the tool straight away, in the same reply; don't just say you'll "
                     "check.")
    missing = [what for key, what in (("search_url", "news, sports scores and prices"),
                                      ("weather_place", "the weather")) if not cfg.get(key)]
    if missing:
        lines.append("You can't check " + " or ".join(missing) + " from this body: if asked, say so playfully "
                     "and never make it up.")
    lines.append(f"Unless you're told someone else is speaking, the person talking to you is your owner, "
                 f"{cfg.get('owner', 'your owner')}: talk to them as \"you\" (or by name), never about them as "
                 "\"my owner\" as if they weren't there.")
    lines.append("Don't invent things about your owner's life, habits or home (what they drink or eat, spills, "
                 "messes, what they always do): only use what they tell you or what's in your notes. Vary your "
                 "jokes; don't keep coming back to the same running gag.")
    return "[" + " ".join(lines) + "]\n"


# "Let me check", "I'll look that up", "checking now"... (a promise to use a tool)
PROMISE = re.compile(r"\b(let me (check|look|find|see|search|take a look|peek)|i'?ll (check|look|find|search|take a "
                     r"look)|i will (check|look)|checking|looking (it|that) up|one (sec|second|moment))\b", re.I)


class Brain:
    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log
        self.history = []  # (time, role, text)
        self._lock = threading.Lock()
        self.context = None  # what's on the PC (app.py fills it in)
        self.shared = SharedNotes(cfg, log, **({"cache_file": cfg["notes_cache"]} if "notes_cache" in cfg else {}))

    # -- what goes into the prompt ------------------------------------------

    def personality(self):
        try:
            with open(self.cfg["personality_file"], encoding="utf-8-sig") as f:
                text = f.read().strip()
        except OSError:
            text = DEFAULT_PERSONALITY
        return text.replace("{owner}", self.cfg["owner"])

    def notes(self):
        """The memory notes shared with the desk Buddy (see shared.py)."""
        return self.shared.notes()

    def diary(self):
        path = self.cfg["eyes_diary"]
        if not path or not os.path.isfile(path):
            return ""
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return ""
        if d.get("day") != dt.date.today().isoformat():
            return ""
        lines = [f"{l['time']} {l['what']}" for l in d.get("lines", [])[-60:]]
        if not lines:
            return ""
        return ("[What you've noticed at the desk today through your camera (time, what was going on). Use it "
                "only when asked about the day or what you've seen; don't bring it up on your own:\n"
                + "\n".join(lines) + "]\n")

    def system_prompt(self):
        now = dt.datetime.now()
        ctx = ""
        try:
            ctx = self.context() if self.context else ""
        except Exception:  # noqa: BLE001
            pass
        return (self.personality() + "\n\n" + ctx
                + f"[It's {now.strftime('%A, %B %d, %Y')}, {now.strftime('%I:%M %p').lstrip('0')}.]\n"
                + limits(self.cfg)
                + notes_block(self.notes()) + self.diary())

    # -- talking --------------------------------------------------------------

    def _recent(self):
        cut = time.time() - HISTORY_MINUTES * 60
        self.history = [h for h in self.history if h[0] >= cut]
        return [{"role": r, "content": t} for _, r, t in self.history]

    def chat(self, heard, extra="", looked=False):
        """Yields the reply as it's written (pieces of text). If the AI asks to
        search, check the weather or look, that's done and it carries on."""
        with self._lock:
            messages = [{"role": "system", "content": self.system_prompt()}] + self._recent()
            messages.append({"role": "user", "content": (extra + heard) if extra else heard})
            defs = tools.definitions(self.cfg)
            if looked:  # already looked for this question
                defs = [d for d in defs if d["function"]["name"] != "look"]
            reply = []
            nudged = False
            for _ in range(4):  # at most a couple of tool rounds
                body = {"model": self.cfg["chat_model"], "messages": messages, "stream": True, "keep_alive": "30m",
                        "options": {"temperature": 0.8, "num_ctx": self.cfg.get("context_size", 8192)}}
                if defs:
                    body["tools"] = defs
                if "instruct" not in self.cfg["chat_model"]:
                    # Models like qwen3:8b think out loud first unless told not to:
                    # that's slow, and he'd wait for it before saying anything.
                    body["think"] = False
                calls, said = [], []
                with requests.post(f"{self.cfg['ollama']}/api/chat", json=body, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line:
                            continue
                        msg = json.loads(line)
                        m = msg.get("message", {})
                        piece = m.get("content", "")
                        if piece:
                            said.append(piece)
                            reply.append(piece)
                            yield piece
                        calls.extend(m.get("tool_calls") or [])
                        if msg.get("done"):
                            break
                if not calls:
                    # "Let me check the weather for you." ...and then no tool call:
                    # small models announce it and stop. Tell it to go ahead, once.
                    text = "".join(said)
                    if defs and not nudged and PROMISE.search(text):
                        nudged = True
                        self.log("(he said he'd check but didn't: asking him to go ahead)")
                        messages.append({"role": "assistant", "content": text})
                        messages.append({"role": "user", "content": "[Go ahead: use your tool now, then answer.]"})
                        if not text.endswith((" ", "\n")):
                            reply.append(" ")
                            yield " "
                        continue
                    break
                messages.append({"role": "assistant", "content": "".join(said), "tool_calls": calls})
                for c in calls:
                    fn = c.get("function", {})
                    name, args = fn.get("name", ""), fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    self.log(f"-> {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
                    messages.append({"role": "tool", "content": tools.run(self.cfg, name, args, self.log),
                                     "tool_name": name})
                if reply and not reply[-1].endswith((" ", "\n")):
                    reply.append(" ")
                    yield " "
            now = time.time()
            self.history.append((now, "user", heard))
            self.history.append((now, "assistant", "".join(reply)))
