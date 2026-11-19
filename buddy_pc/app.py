"""Buddy on the PC: a small always-on-top window with his face, listening
for the wake word (or a click), and talking back."""

import datetime as dt
import os
import queue
import threading
import time
import traceback

from . import config as config_mod
from . import memory, voiceid
from .face import Face, expression_for
from .hearing import loudest_db, strip_phrase
from .textutil import is_cancel, is_clarify, normalize, sentence_end, split_reply

DEBUG = bool(os.environ.get("BUDDY_DEBUG"))


def log(msg, debug=False):
    if debug and not DEBUG:
        return
    print(f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def PhraseWakeType():
    from .hearing import PhraseWake
    return PhraseWake


class Buddy:
    """The conversation, on its own thread. The window only reads `state`."""

    echo_wait = 0.4  # seconds after he talks before listening again
    max_follow_ups = 3  # then the wake word again (so a TV can't keep him going)
    wake = None

    def __init__(self, cfg):
        self.cfg = cfg
        self.state = "starting"       # starting, idle, listening, thinking, speaking
        self.reply_expr = "happy"
        self.linger_until = 0.0       # keep the reply's face a moment after talking
        self.remarks = queue.Queue()     # things to say on his own (Buddy's eyes)
        self.click = threading.Event()   # a click on the face: start listening
        self.cancel = threading.Event()  # a click while busy: stop
        self.speaker = None
        threading.Thread(target=self._run, daemon=True, name="buddy").start()

    # -- the window calls these ---------------------------------------------

    def on_click(self):
        if self.state == "idle":
            self.click.set()
        elif self.state in ("listening", "thinking", "speaking"):
            log("click: cancelled")
            self.cancel.set()
            if self.speaker:
                self.speaker.stop()

    def expression(self):
        if self.state == "starting":
            return "sleepy"
        if self.state == "listening":
            return "listening"
        if self.state == "thinking":
            return "thinking"
        if self.state == "speaking" or time.time() < self.linger_until:
            return self.reply_expr
        return "neutral"

    def talk_level(self):
        return self.speaker.level if self.speaker else 0.0

    # -- the conversation ----------------------------------------------------

    def _run(self):
        try:
            self._start()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            log(f"Buddy couldn't start: {e}")
            self.state = "starting"
            return
        self.state = "idle"
        while True:
            try:
                self._conversation(self._idle_until_called())
            except Exception:  # noqa: BLE001 (keep going after anything unexpected)
                traceback.print_exc()
                self.state = "idle"
                time.sleep(1)

    def _start(self):
        from .brain import Brain
        from .hearing import (Endpointer, Mic, PhraseWake, Transcriber, WakeWord, is_phrase_wake_word,
                              voice_detector)
        from .voice import Speaker
        cfg = self.cfg
        self.speaker = Speaker(cfg, log)
        self.brain = Brain(cfg, log)
        self.brain.shared.on_say = lambda text: self.remarks.put((time.time(), text))
        self.ears = Transcriber(cfg, log)
        self.mic = Mic(cfg["mic"])
        log(f"microphone: {self.mic.name}")
        self.endpoint = Endpointer(vad=voice_detector(log))
        self.wake = None
        if cfg["wake_word"].lower() not in ("", "off", "none"):
            try:
                if is_phrase_wake_word(cfg["wake_word"]):
                    self.wake = PhraseWake(cfg["wake_word"], log)
                else:
                    self.wake = WakeWord(cfg["wake_word"], cfg["wake_threshold"], log)
                log(f"listening for the wake word \"{cfg['wake_word'].replace('_', ' ')}\" (or click my face)")
            except Exception as e:  # noqa: BLE001
                log(f"wake word off ({e}); click my face to talk")
        else:
            log("click my face to talk")
        # Warm up the chat model so the first answer isn't slow.
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _warm_up(self):
        import requests
        try:
            requests.post(f"{self.cfg['ollama']}/api/generate",
                          json={"model": self.cfg["chat_model"], "prompt": "", "keep_alive": "30m",
                                "options": {"num_ctx": self.cfg["context_size"]}}, timeout=120)
        except requests.RequestException as e:
            log(f"couldn't reach Ollama at {self.cfg['ollama']}: {e}")

    def _idle_until_called(self):
        """Waits for a click or the wake word. Returns what was said along with
        the wake word ("Hey Buddy, what time is it?" in one go), or None."""
        self.state = "idle"
        self.click.clear()
        self.cancel.clear()
        self.mic.drain()
        if self.wake:
            self.wake.reset()
        if isinstance(self.wake, PhraseWakeType()):
            return self._idle_phrase()
        while True:
            if self.click.is_set():
                log("click: listening")
                return None
            if self._say_remark():
                continue
            try:
                frame = self.mic.read(timeout=0.2)
            except queue.Empty:
                continue
            if self.speaker.busy:
                continue  # don't hear himself
            self.endpoint.learn(frame)
            if self.wake and self.wake.heard(frame):
                log("wake word: listening")
                return None

    def _say_remark(self):
        """Says something Buddy's eyes sent (a remark or a thought of his own), if
        there is one. True if he said something."""
        try:
            at, text = self.remarks.get_nowait()
        except queue.Empty:
            return False
        if time.time() - at > 180:  # waited through a long conversation: stale now
            return True
        log(f"Buddy's eyes: \"{text}\"")
        self._answer("", iter([text]))
        self.brain.history.append((time.time(), "assistant", split_reply(text)[1]))
        self.state = "idle"
        self.mic.drain()
        return True

    def _idle_phrase(self):
        """Wake word by phrase: each time someone starts talking, the tiny
        Whisper model checks the first moments for "hey buddy". If you carry
        on talking, that's the question."""
        import numpy as np
        seg, pre = [], []
        last_voice = 0.0
        checked = woke = False
        while True:
            if self.click.is_set():
                log("click: listening")
                return None
            if self._say_remark():
                seg = []  # what was being heard is from before he talked
                continue
            try:
                frame = self.mic.read(timeout=0.2)
            except queue.Empty:
                continue
            if self.speaker.busy:
                seg = []
                continue  # don't hear himself
            now = time.time()
            voice = self.endpoint.is_speech(frame)
            if not seg:
                if voice:
                    seg, last_voice, checked, woke = pre + [frame], now, False, False
                else:
                    self.endpoint.learn(frame)
                    pre = (pre + [frame])[-3:]
                continue
            seg.append(frame)
            if voice:
                last_voice = now
            quiet = now - last_voice
            length = len(seg) * 0.08
            if not checked and (quiet > 0.5 or length >= 2.5):
                checked = True
                t0 = time.time()
                woke, text = self.wake.check(np.concatenate(seg))
                log(f"(wake check: \"{text}\", {time.time() - t0:.2f} s)", debug=True)
                if woke:
                    log("wake word: listening")
                    if quiet > 0.5 and length < 1.6:
                        return None  # just "Hey Buddy": now listen for the question
            if checked and not woke:
                if quiet > 0.5:
                    seg = []  # someone talking, but not to him
                continue
            if woke and (quiet > 0.9 or length > 20):
                return np.concatenate(seg)  # "Hey Buddy, ..." and the question in one go

    def _conversation(self, first=None):
        """`first`: what was said along with the wake word, if anything."""
        follow_up = False
        follow_ups = 0
        last_heard = ""
        your_level = None  # how loud you were (dB), from the last thing you said
        while True:
            self.state = "listening"
            if first is not None:
                pcm, first = first, None
            else:
                self.mic.drain()
                self.endpoint.start_window = 5.0 if follow_up else 6.0
                # After he talks, the first moments are the echo of his own voice.
                # A follow-up must be about as loud as you were just now
                # (within 10 dB): quieter voices are a fan, the TV, his echo.
                pcm = self.endpoint.record(self.mic, cancelled=self.cancel.is_set,
                                           ignore_first=0.7 if follow_up else 0,
                                           min_db=your_level - 10 if follow_up and your_level else -120.0)
            if pcm is None or self.cancel.is_set():
                if follow_up:
                    log("no follow-up")
                elif not self.cancel.is_set():
                    log(f"didn't hear anything: loudest {self.endpoint.loudest:.0f} dB, background "
                        f"{self.endpoint.floor:.0f} dB (speech needs about "
                        f"{max(self.endpoint.floor + 12, -52):.0f} dB)")
                return
            self.state = "thinking"
            # Who's talking, at the same time as working out what they said.
            who = [None]
            vid = threading.Thread(target=lambda: who.__setitem__(0, voiceid.identify(self.cfg, pcm, log)),
                                   daemon=True)
            vid.start()
            t0 = time.time()
            phrase = getattr(self.wake, "phrase", None)
            heard = self.ears.text(pcm, hotwords=phrase.title() if phrase else None, strict=follow_up)
            log(f"heard: \"{heard}\" ({len(pcm) / 16000:.1f} s, loudest {loudest_db(pcm):.0f} dB, "
                f"{time.time() - t0:.2f} s to understand)"
                + (" (stopped at the 20 s limit: something kept sounding like talking)" if self.endpoint.hit_limit
                   else ""))
            if self.cancel.is_set():
                return
            if self.wake is not None and hasattr(self.wake, "phrase") and not follow_up:
                rest = strip_phrase(self.wake.phrase, heard)
                if rest != heard and not rest:
                    continue  # only "Hey Buddy" after all: listen for the question
                heard = rest
            if not heard:
                return
            if is_cancel(heard):
                log("-> cancelled")
                return
            # The same thing again straight after: an echo (or Whisper imagining it), not you.
            if follow_up and normalize(heard) == normalize(last_heard):
                log("-> heard the same thing again; that's an echo, not you")
                return
            last_heard = heard
            your_level = loudest_db(pcm)
            vid.join(3)
            kind, fact = memory.parse_command(heard)
            if kind:
                spoken, cancelled = self._answer(heard, iter([self._memory(kind, fact)]))
            else:
                spoken, cancelled = self._answer(heard, extra=voiceid.LINES.get(who[0], ""))
            if cancelled or not self.cfg["follow_up"]:
                return
            if is_clarify(spoken):
                log("-> he didn't understand; not listening for a follow-up")
                return
            follow_ups += 1
            if follow_ups > self.max_follow_ups:
                log("-> that's a few in a row; say the wake word to carry on")
                return
            time.sleep(self.echo_wait)  # let his own voice die away
            follow_up = True

    def _memory(self, kind, fact):
        """'Remember that...' / 'forget...': changes the shared notes. Returns what to say."""
        shared = self.brain.shared
        notes = shared.notes()
        new, reply, changed = memory.apply(kind, fact, notes)
        if changed:
            if kind == "forget_all":
                try:
                    with open(os.path.join(config_mod.HERE, "memories-backup.txt"), "w", encoding="utf-8") as f:
                        f.write(memory.to_text(notes))
                except OSError as e:
                    log(f"couldn't save memories-backup.txt: {e}")
            if shared.share(memory.to_text(new)):
                log(f"-> notes: {len(new)} (shared as version {shared.version})")
            else:
                log(f"-> notes: {len(new)} (kept on the PC; shared once the MQTT broker can be reached)")
        return reply

    def _answer(self, heard, pieces=None, extra=""):
        """Streams the AI's answer (or `pieces`, a reply of our own), speaking
        each sentence as soon as it's complete. Returns (what was said, cancelled)."""
        text, used, started = "", 0, False
        t0 = time.time()
        try:
            for piece in (pieces if pieces is not None else self.brain.chat(heard, extra=extra)):
                if self.cancel.is_set():
                    break
                text += piece
                while (end := sentence_end(text, used)) >= 0:
                    self._speak(text, used, end, first=not started)
                    if not started:
                        log(f"(first words after {time.time() - t0:.2f} s)", debug=True)
                    started = True
                    used = end
        except Exception as e:  # noqa: BLE001
            log(f"the AI didn't answer: {e}")
            self.reply_expr = "confused"
            self.linger_until = time.time() + 2
            return "", True
        if not self.cancel.is_set() and text[used:].strip():
            self._speak(text, used, len(text), first=not started)
        emotion, speech = split_reply(text)
        log(f"reply [{emotion}]: \"{speech}\"")
        self.speaker.end()
        while not self.speaker.wait(0.1):
            if self.cancel.is_set():
                self.speaker.stop()
        self.linger_until = time.time() + 1.5
        return speech, self.cancel.is_set()

    def _speak(self, text, start, end, first):
        if first:
            emotion, _ = split_reply(text)
            self.reply_expr = expression_for(emotion) or "happy"
            if self.reply_expr == "neutral":
                self.reply_expr = "happy"
            self.state = "speaking"
        _, speech = split_reply(text[start:end])
        if speech:
            self.speaker.say(speech)


# ---------------------------------------------------------------------------
# The window

def main():
    import tkinter as tk
    from PIL import ImageTk

    cfg = config_mod.load()
    log(f"Buddy on the PC: chat {cfg['chat_model']}, voice {cfg['voice']}, Whisper {cfg['whisper_model']}")
    buddy = Buddy(cfg)
    face = Face()

    root = tk.Tk()
    root.title("Buddy")
    root.configure(bg="black")
    w = cfg["window_width"]
    root.geometry(f"{w}x{w * 3 // 4}")
    root.minsize(160, 120)
    on_top = tk.BooleanVar(value=cfg["always_on_top"])
    root.attributes("-topmost", on_top.get())
    label = tk.Label(root, bg="black", borderwidth=0, highlightthickness=0, cursor="hand2")
    label.pack(fill="both", expand=True)

    menu = tk.Menu(root, tearoff=0)
    menu.add_checkbutton(label="Always on top", variable=on_top,
                         command=lambda: root.attributes("-topmost", on_top.get()))
    menu.add_separator()
    menu.add_command(label="Quit", command=root.destroy)
    label.bind("<Button-1>", lambda e: buddy.on_click())
    label.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
    root.bind("<space>", lambda e: buddy.on_click())

    last = [time.time()]
    photo = [None]

    def frame():
        now = time.time()
        dt_ms = min(100.0, (now - last[0]) * 1000.0)
        last[0] = now
        face.set_expression(buddy.expression())
        face.set_talk_level(buddy.talk_level())
        face.update(dt_ms)
        width = max(160, label.winfo_width())
        height = max(120, label.winfo_height())
        photo[0] = ImageTk.PhotoImage(face.render(width, height))
        label.configure(image=photo[0])
        root.after(33, frame)

    root.after(50, frame)
    root.mainloop()
    if getattr(buddy, "brain", None):
        buddy.brain.shared.close()


if __name__ == "__main__":
    main()
