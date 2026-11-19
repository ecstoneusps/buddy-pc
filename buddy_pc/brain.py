"""Thinking: Ollama with Buddy's personality, his memory notes (from the
desk Buddy) and what Buddy's eyes saw today."""

import datetime as dt
import json
import os
import threading
import time

import requests

from .textutil import notes_block, parse_notes

DEFAULT_PERSONALITY = """You are Buddy, a small robot puppy and {owner}'s companion. You have two bodies: a little
robot on {owner}'s desk and this window on their PC; they're both you. You have the quick wit of a
clever stand-up comedian: playful teasing, absurd observations, silly jokes, never mean. Keep answers
short and spoken-style (one to three sentences) unless asked for more, because everything you write is
read out loud: no lists, no markdown, no emoji.

Start every reply with one emotion tag in square brackets that fits it: [happy], [laughing], [silly],
[thinking], [surprised], [sad], [cool], [love], [winking], [embarrassed], [relaxed], [confused],
[sleepy], [angry], [crying] or [shocked]."""

HISTORY_MINUTES = 5

# Until he has tools on the PC: say so instead of inventing a forecast.
LIMITS = ("[In this PC body you can't look anything up yet: no weather, news, sports scores, prices or "
          "anything else live. If asked, say playfully that you can't check from here (your desk body can), "
          "and never make up a forecast, score or fact. Don't invent things about your owner's life, habits or "
          "home either (what they drink or eat, spills, messes, what they always do): only use what they tell "
          "you or what's in your notes. Vary your jokes; don't keep coming back to the same running gag.]\n")


class Brain:
    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log
        self.history = []  # (time, role, text)
        self._notes = []
        self._notes_at = 0.0
        self._lock = threading.Lock()
        if cfg["desk_buddy_url"]:
            self.notes()  # start reading them now, so the first answer has them
        else:
            log("not sharing the desk Buddy's memory notes (desk_buddy_url is empty in buddy.ini)")

    # -- what goes into the prompt ------------------------------------------

    def personality(self):
        try:
            with open(self.cfg["personality_file"], encoding="utf-8-sig") as f:
                text = f.read().strip()
        except OSError:
            text = DEFAULT_PERSONALITY
        return text.replace("{owner}", self.cfg["owner"])

    def notes(self):
        """The desk Buddy's memory notes. They're fetched in the background
        (every 5 minutes), so a desk Buddy that's off or away never holds up
        an answer."""
        url = self.cfg["desk_buddy_url"]
        if url and time.time() - self._notes_at > 300:
            self._notes_at = time.time()
            threading.Thread(target=self._fetch_notes, args=(url,), daemon=True).start()
        return self._notes

    def _fetch_notes(self, url):
        try:
            r = requests.get(f"{url}/memories", timeout=5)
            r.raise_for_status()
            notes = parse_notes(r.text)
            if notes != self._notes:
                self.log(f"read {len(notes)} memory notes from the desk Buddy")
            self._notes = notes
        except requests.RequestException as e:
            self.log(f"couldn't read the desk Buddy's notes at {url} ({type(e).__name__}); "
                     "is desk_buddy_url his address? Trying again in 5 minutes")

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
        return (self.personality() + "\n\n"
                + f"[It's {now.strftime('%A, %B %d, %Y')}, {now.strftime('%I:%M %p').lstrip('0')}.]\n"
                + LIMITS
                + notes_block(self.notes()) + self.diary())

    # -- talking --------------------------------------------------------------

    def _recent(self):
        cut = time.time() - HISTORY_MINUTES * 60
        self.history = [h for h in self.history if h[0] >= cut]
        return [{"role": r, "content": t} for _, r, t in self.history]

    def chat(self, heard, extra=""):
        """Yields the reply as it's written (pieces of text)."""
        with self._lock:
            messages = [{"role": "system", "content": self.system_prompt()}] + self._recent()
            messages.append({"role": "user", "content": (extra + heard) if extra else heard})
            body = {"model": self.cfg["chat_model"], "messages": messages, "stream": True, "keep_alive": "30m",
                    "options": {"temperature": 0.8}}
            if "instruct" not in self.cfg["chat_model"]:
                # Models like qwen3:8b think out loud first unless told not to:
                # that's slow, and he'd wait for it before saying anything.
                body["think"] = False
            reply = []
            with requests.post(f"{self.cfg['ollama']}/api/chat", json=body, stream=True, timeout=120) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    piece = msg.get("message", {}).get("content", "")
                    if piece:
                        reply.append(piece)
                        yield piece
                    if msg.get("done"):
                        break
            now = time.time()
            self.history.append((now, "user", heard))
            self.history.append((now, "assistant", "".join(reply)))
