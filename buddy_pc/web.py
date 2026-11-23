"""Buddy in a web page, for the phone (and, mirrored, the TV): his face, a
talk button and a type box. It runs inside Buddy on the PC (same brain,
same notes shared with the desk Buddy), listening only on this PC
(127.0.0.1). Tailscale brings it to your phone wherever you are, over HTTPS
(the phone's microphone needs that): run once on the PC

    tailscale serve --bg 8770

and open https://<this PC's name>.<your tailnet>.ts.net on the phone.
Only your own Tailscale devices can reach it; nothing is opened to the
internet."""

import base64
import io
import json
import os
import threading
import time
import wave
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

HERE = os.path.dirname(__file__)
MAX_UPLOAD = 12_000_000  # a recording and a photo, as text, are far less than this
LOOK_DEFAULT = "What do you see? React to it."


def wav_bytes(rate, pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype("<i2").tobytes())
    return buf.getvalue()


def decode(data):
    """Phone audio (an iPhone's MP4/AAC, a browser's WebM/Opus...) -> 16 kHz int16."""
    import av  # (installed with faster-whisper)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    parts = []
    with av.open(io.BytesIO(data), mode="r") as box:
        for frame in box.decode(audio=0):
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # what's left in the resampler
            parts.append(out.to_ndarray().reshape(-1))
    return np.concatenate(parts).astype(np.int16) if parts else np.zeros(0, np.int16)


def face_tables():
    """Buddy's expressions, for the page's copy of his face."""
    from .face import FaceParams, _TWEAKS
    return {"base": asdict(FaceParams()), "tweaks": _TWEAKS}


class Web:
    def __init__(self, buddy, cfg, log):
        self.buddy, self.cfg, self.log = buddy, cfg, log
        self.lock = threading.Lock()  # one question at a time
        # For the TV app: what to show and say there (the phone asks, the TV answers).
        self.events, self.last_event = [], 0
        self.changed = threading.Condition()
        self.tv_seen = 0.0
        port = int(cfg.get("web_port", 8770))
        handler = type("H", (Handler,), {"web": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="web").start()
        log(f"Buddy's phone app: http://localhost:{port} on this PC (and through \"tailscale serve --bg {port}\" "
            "on your phone)")

    def tv_connected(self):
        return time.time() - self.tv_seen < 40

    def push(self, kind, **data):
        """Something for the TV: "listening", "thinking", or a "reply" to show and say."""
        with self.changed:
            self.last_event += 1
            self.events = (self.events + [dict(data, kind=kind, id=self.last_event)])[-20:]
            self.changed.notify_all()

    def wait_events(self, after, timeout=25.0):
        """The TV's long poll: what's new after `after` (-1: nothing old, just where things are)."""
        self.tv_seen = time.time()
        with self.changed:
            if after >= 0 and self.last_event <= after:
                self.changed.wait(timeout)
            self.tv_seen = time.time()
            new = [e for e in self.events if e["id"] > after] if after >= 0 else []
            return {"events": new, "last": self.last_event}

    def look(self, jpg, heard):
        """What he sees in the phone's photo, for his prompt (and Buddy's eyes' diary)."""
        from . import tools
        from .app import diary_line
        if not self.cfg.get("vision_model"):
            return "[Your owner sent a photo from their phone, but you have no way to see pictures.]\n"
        try:
            seen = tools.look(self.cfg, heard, self.log, jpg=jpg)
        except Exception as e:  # noqa: BLE001 (answer anyway, honestly)
            self.log(f"phone: couldn't look at the photo: {e}")
            return "[Your owner sent a photo from their phone, but you couldn't see it just now.]\n"
        line = diary_line(seen, self.cfg.get("owner", "my owner"))
        shared = getattr(self.buddy.brain, "shared", None)
        if line and shared is not None:
            shared.saw("Out and about: " + line)
        return (f"[Your owner just showed you something with their phone's camera. {seen} Answer from that "
                "alone; if it doesn't show what was asked, say you can't quite tell.]\n")

    def reply(self, heard, jpg=None):
        """What he says back (emotion, words), the way he answers at the desk."""
        from . import jokes, memory
        from .textutil import is_cancel, split_reply
        b = self.buddy
        kind, fact = memory.parse_command(heard) if jpg is None else (None, None)
        if kind:
            text = b._memory(kind, fact)
        elif jpg is None and (done := b._home(heard)):
            text = done
        elif is_cancel(heard):
            return "neutral", ""
        else:
            extra = "[You're talking through the Buddy app on your owner's phone (they may be away from home).]\n"
            if jpg is not None:
                extra += self.look(jpg, heard)
            joke = jokes.wanted(heard)
            if joke:
                extra += jokes.hint()
            text = "".join(b.brain.chat(heard, extra=extra, looked=jpg is not None))
            if joke:
                jokes.told(split_reply(text)[1])
        emotion, speech = split_reply(text)
        return emotion or "happy", speech

    def answer(self, heard, jpg=None, to_tv=False):
        """{heard, emotion, text, audio (WAV, base64)} for the page; with
        `to_tv` (and the TV app open), said on the TV instead."""
        to_tv = to_tv and self.tv_connected()
        if to_tv:
            self.push("thinking")
        out = self._answer(heard, jpg)
        if to_tv:
            self.push("reply", **out)
            out = dict(out, audio=None, on_tv=True)
        return out

    def _answer(self, heard, jpg=None):
        from .face import expression_for
        from .voice import boost, level_gain, synthesize
        with self.lock:
            t0 = time.time()
            emotion, speech = self.reply(heard, jpg)
            self.log(f"phone{' (with a photo)' if jpg else ''}: \"{heard}\" -> [{emotion}] \"{speech}\" "
                     f"({time.time() - t0:.1f} s)")
            audio = None
            if speech:
                try:
                    rate, pcm = synthesize(self.cfg["kokoro_host"], self.cfg["kokoro_port"], speech, self.cfg["voice"])
                    # Louder than at the desk: a phone's speaker is small (the
                    # limiter keeps it clean).
                    gain, _ = level_gain(pcm, 2.0)
                    audio = base64.b64encode(wav_bytes(rate, boost(pcm, gain))).decode()
                except (OSError, RuntimeError, ValueError) as e:
                    self.log(f"phone: no voice for that ({e}); the words are shown")
            return {"heard": heard, "emotion": expression_for(emotion) or "happy", "text": speech, "audio": audio}


class Handler(BaseHTTPRequestHandler):
    web = None  # set per server

    def log_message(self, *args):  # (the requests aren't worth a log line each)
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/events":  # the TV app waiting for something to show
            from urllib.parse import parse_qs
            after = int((parse_qs(query).get("after") or ["-1"])[0])
            return self._json(self.web.wait_events(after))
        if path == "/api/status":
            return self._json({"tv": self.web.tv_connected()})
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "webapp", "index.html"), encoding="utf-8") as f:
                page = f.read().replace("/*FACE*/null", json.dumps(face_tables()))
            self._send(200, page.encode(), "text/html; charset=utf-8")
        elif path == "/manifest.webmanifest":
            self._json({"name": "Buddy", "short_name": "Buddy", "display": "standalone", "start_url": "/",
                        "background_color": "#000000", "theme_color": "#000000",
                        "icons": [{"src": "/icon.png", "sizes": "180x180", "type": "image/png"}]})
        elif path in ("/icon.png", "/apple-touch-icon.png"):
            from .face import Face
            img = Face(seed=1).render(180, 135)
            from PIL import Image
            square = Image.new("RGB", (180, 180), (0, 0, 0))
            square.paste(img, (0, 22))
            buf = io.BytesIO()
            square.save(buf, "PNG")
            self._send(200, buf.getvalue(), "image/png")
        else:
            self._send(404, b"not here", "text/plain")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        size = int(self.headers.get("Content-Length") or 0)
        if size <= 0 or size > MAX_UPLOAD:
            return self._json({"error": "nothing sent, or too much"}, 400)
        data = self.rfile.read(size)
        try:
            if path == "/api/talk":  # a recording from the phone (and maybe a photo)
                jpg, msg = None, {}
                if (self.headers.get("Content-Type") or "").startswith("application/json"):
                    msg = json.loads(data)
                    data = base64.b64decode(msg.get("audio") or "")
                    jpg = base64.b64decode(msg["image"]) if msg.get("image") else None
                try:
                    pcm = decode(data) if data else np.zeros(0, np.int16)
                except Exception as e:  # noqa: BLE001
                    self.web.log(f"phone: couldn't read the recording ({type(e).__name__}: {e})")
                    return self._json({"error": "couldn't read that recording"}, 400)
                heard = self.web.buddy.ears.text(pcm) if len(pcm) > 16000 * 0.3 else ""
                if not heard and jpg is not None:
                    heard = LOOK_DEFAULT  # (a photo and nothing said: just look)
                if not heard:
                    return self._json({"heard": "", "emotion": "confused", "text": "", "audio": None})
                return self._json(self.web.answer(heard, jpg, to_tv=bool(msg.get("to_tv"))))
            if path == "/api/say":  # typed (and maybe a photo)
                msg = json.loads(data)
                jpg = base64.b64decode(msg["image"]) if msg.get("image") else None
                text = str(msg.get("text", "")).strip()[:500] or (LOOK_DEFAULT if jpg else "")
                if not text:
                    return self._json({"error": "nothing typed"}, 400)
                return self._json(self.web.answer(text, jpg, to_tv=bool(msg.get("to_tv"))))
            if path == "/api/state":  # the phone is listening to you: the TV shows it too
                if self.web.tv_connected():
                    self.web.push(str(json.loads(data).get("state", ""))[:20])
                return self._json({"ok": True})
        except Exception as e:  # noqa: BLE001 (tell the page, keep serving)
            self.web.log(f"phone: something went wrong: {type(e).__name__}: {e}")
            return self._json({"error": "Buddy had a problem answering that"}, 500)
        self._send(404, b"not here", "text/plain")
