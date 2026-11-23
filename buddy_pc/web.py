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
MAX_UPLOAD = 8_000_000  # about a minute of phone audio is far less than this


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
        port = int(cfg.get("web_port", 8770))
        handler = type("H", (Handler,), {"web": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="web").start()
        log(f"Buddy's phone app: http://localhost:{port} on this PC (and through \"tailscale serve --bg {port}\" "
            "on your phone)")

    def reply(self, heard):
        """What he says back (emotion, words), the way he answers at the desk."""
        from . import jokes, memory
        from .textutil import is_cancel, split_reply
        b = self.buddy
        kind, fact = memory.parse_command(heard)
        if kind:
            text = b._memory(kind, fact)
        elif is_cancel(heard):
            return "neutral", ""
        else:
            extra = "[You're talking through the Buddy app on your owner's phone (they may be away from home).]\n"
            joke = jokes.wanted(heard)
            if joke:
                extra += jokes.hint()
            text = "".join(b.brain.chat(heard, extra=extra))
            if joke:
                jokes.told(split_reply(text)[1])
        emotion, speech = split_reply(text)
        return emotion or "happy", speech

    def answer(self, heard):
        """{heard, emotion, text, audio (WAV, base64)} for the page."""
        from .face import expression_for
        from .voice import boost, level_gain, synthesize
        with self.lock:
            t0 = time.time()
            emotion, speech = self.reply(heard)
            self.log(f"phone: \"{heard}\" -> [{emotion}] \"{speech}\" ({time.time() - t0:.1f} s)")
            audio = None
            if speech:
                try:
                    rate, pcm = synthesize(self.cfg["kokoro_host"], self.cfg["kokoro_port"], speech, self.cfg["voice"])
                    gain, _ = level_gain(pcm, 1.0)
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
        path = self.path.split("?", 1)[0]
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
            if path == "/api/talk":  # a recording from the phone
                try:
                    pcm = decode(data)
                except Exception as e:  # noqa: BLE001
                    self.web.log(f"phone: couldn't read the recording ({type(e).__name__}: {e})")
                    return self._json({"error": "couldn't read that recording"}, 400)
                heard = self.web.buddy.ears.text(pcm) if len(pcm) > 16000 * 0.3 else ""
                if not heard:
                    return self._json({"heard": "", "emotion": "confused", "text": "", "audio": None})
                return self._json(self.web.answer(heard))
            if path == "/api/say":  # typed
                text = str(json.loads(data).get("text", "")).strip()[:500]
                if not text:
                    return self._json({"error": "nothing typed"}, 400)
                return self._json(self.web.answer(text))
        except Exception as e:  # noqa: BLE001 (tell the page, keep serving)
            self.web.log(f"phone: something went wrong: {type(e).__name__}: {e}")
            return self._json({"error": "Buddy had a problem answering that"}, 500)
        self._send(404, b"not here", "text/plain")
