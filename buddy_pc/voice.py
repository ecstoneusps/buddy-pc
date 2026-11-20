"""Speaking: Kokoro over the Wyoming protocol (the same container Home
Assistant uses), played through the PC's speakers with the mouth following
the sound."""

import json
import queue
import socket
import threading
import time

import numpy as np

# ---------------------------------------------------------------------------
# Wyoming: each event is a JSON header line, then optional JSON data and a
# binary payload (their sizes are in the header).


def _send_event(sock, etype, data=None):
    body = json.dumps(data or {}).encode()
    header = {"type": etype, "version": "1.5.0", "data_length": len(body)}
    sock.sendall(json.dumps(header).encode() + b"\n" + body)


def _read_exact(f, n):
    buf = f.read(n)
    if buf is None or len(buf) < n:
        raise ConnectionError("Kokoro closed the connection")
    return buf


def read_event(f):
    """(type, data dict, payload bytes) from a Wyoming stream, or None at the end."""
    line = f.readline()
    if not line:
        return None
    header = json.loads(line)
    data = dict(header.get("data") or {})
    if header.get("data_length"):
        data.update(json.loads(_read_exact(f, header["data_length"])))
    payload = _read_exact(f, header["payload_length"]) if header.get("payload_length") else b""
    return header.get("type"), data, payload


def synthesize(host, port, text, voice, timeout=30):
    """Speech for `text`: (sample rate, mono int16 numpy array)."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        _send_event(sock, "synthesize", {"text": text, "voice": {"name": voice}})
        f = sock.makefile("rb")
        rate, width, channels, chunks = 22050, 2, 1, []
        while True:
            ev = read_event(f)
            if ev is None:
                break
            etype, data, payload = ev
            if etype == "audio-start" or (etype == "audio-chunk" and "rate" in data):
                rate = data.get("rate", rate)
                width = data.get("width", width)
                channels = data.get("channels", channels)
            if etype == "audio-chunk":
                chunks.append(payload)
            elif etype in ("audio-stop", "error"):
                if etype == "error":
                    raise RuntimeError(f"Kokoro: {data.get('text', data)}")
                break
    if width != 2:
        raise RuntimeError(f"Kokoro sent {width * 8}-bit audio; expected 16-bit")
    pcm = np.frombuffer(b"".join(chunks), dtype=np.int16)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return rate, pcm


def level_of(chunk):
    """Loudness of a chunk mapped to 0..1 mouth opening (-42 dBFS closed, -12 wide open)."""
    if len(chunk) == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) / 32768.0
    db = 20 * np.log10(rms) if rms > 1e-6 else -120.0
    return float(min(1.0, max(0.0, (db + 42.0) / 30.0)))


def boost(pcm, gain, ceiling=30000.0):
    """Louder by `gain`, without clipping: a limiter turns the loudest parts
    down just enough (per 20 ms, ramped so it doesn't click) to stay under the
    ceiling, and lets the gain back up slowly. Returns int16."""
    if gain <= 1.0:
        return (pcm.astype(np.float32) * gain).astype(np.int16)
    x = pcm.astype(np.float32)
    out = np.empty_like(x)
    block = 320  # 20 ms at 16 kHz (about the same at 24 kHz)
    g = gain
    for i in range(0, len(x), block):
        chunk = x[i:i + block]
        peak = float(np.max(np.abs(chunk))) if len(chunk) else 0.0
        target = min(gain, g * 1.06, ceiling / peak if peak > 0 else gain)  # release slowly, attack at once
        start = min(g, target)
        ramp = np.linspace(start, target, len(chunk), endpoint=True, dtype=np.float32)
        out[i:i + block] = chunk * ramp
        g = target
    return np.clip(out, -32768, 32767).astype(np.int16)


class Ducker:
    """Turns every other app's volume down while he talks (like a video call
    does), and back up afterwards. Windows only (its per-app volumes, the
    Volume Mixer's sliders)."""

    def __init__(self, level, log):
        self.level, self.log = level, log  # 0..1 of their volume while he talks; 1 = off
        self.saved = {}  # session -> volume it had
        self.ok = level < 1.0
        if self.ok:
            import atexit
            atexit.register(self.restore)

    def duck(self):
        if not self.ok or self.saved:
            return
        try:
            import os
            import comtypes
            from pycaw.pycaw import AudioUtilities
            comtypes.CoInitialize()
            me = os.getpid()
            for s in AudioUtilities.GetAllSessions():
                if s.Process is None or s.Process.pid == me:
                    continue
                vol = s.SimpleAudioVolume
                was = vol.GetMasterVolume()
                vol.SetMasterVolume(was * self.level, None)
                self.saved[s] = (vol, was)
        except Exception as e:  # noqa: BLE001 (no ducking, then)
            self.log(f"couldn't turn the other apps down while talking ({e}); duck_others is off")
            self.ok = False
            self.restore()

    def restore(self):
        for vol, was in self.saved.values():
            try:
                vol.SetMasterVolume(was, None)
            except Exception:  # noqa: BLE001 (that app has closed)
                pass
        self.saved = {}


class Speaker:
    """Plays sentences one after another (each is synthesized while the one
    before it plays). say() queues one; end() marks the end of a reply."""

    def __init__(self, cfg, log):
        self.cfg = cfg
        self.log = log
        self.level = 0.0          # for the mouth
        self.busy = False         # something queued or playing
        self._texts = queue.Queue()
        self._audio = queue.Queue(maxsize=3)
        self._stop = threading.Event()
        self._done = threading.Event()
        self._done.set()
        threading.Thread(target=self._synth_loop, daemon=True, name="tts").start()
        threading.Thread(target=self._play_loop, daemon=True, name="play").start()

    def say(self, text):
        if text.strip():
            self._done.clear()
            self.busy = True
            self._texts.put(text.strip())

    def end(self):
        """No more sentences for this reply."""
        self._done.clear()
        self.busy = True
        self._texts.put(None)

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    def stop(self):
        """Stop talking now (tap to cancel)."""
        self._stop.set()
        for q in (self._texts, self._audio):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        self._audio.put(None)

    def _synth_loop(self):
        while True:
            text = self._texts.get()
            if text is None:
                self._audio.put(None)
                continue
            if self._stop.is_set():
                continue
            t0 = time.time()
            try:
                rate, pcm = synthesize(self.cfg["kokoro_host"], self.cfg["kokoro_port"], text, self.cfg["voice"])
            except (OSError, RuntimeError, ValueError) as e:
                self.log(f"couldn't get speech from Kokoro ({e}); is its container running?")
                continue
            self.log(f"(voice: {len(pcm) / rate:.1f} s of speech in {time.time() - t0:.2f} s)", debug=True)
            self._audio.put((rate, pcm))

    def _speaker_device(self):
        from .hearing import find_device
        try:
            return find_device(self.cfg["speaker"], "output")
        except ValueError as e:
            self.log(f"{e}; using Windows' default speakers")
            return None

    def _play_loop(self):
        import sounddevice as sd
        out, out_rate = None, None
        ducker = Ducker(self.cfg.get("duck_others", 40) / 100.0, self.log)
        while True:
            item = self._audio.get()
            if item is None:  # end of a reply (or stopped)
                self.level = 0.0
                if self._texts.empty() and self._audio.empty():
                    ducker.restore()
                    if out is not None:  # let the last bit play out, then close
                        try:
                            out.stop()
                            out.close()
                        except Exception:  # noqa: BLE001
                            pass
                        out = None
                    self._stop.clear()
                    self.busy = False
                    self._done.set()
                continue
            if self._stop.is_set():
                continue
            rate, pcm = item
            ducker.duck()  # (once per reply)
            vol = self.cfg["volume"] / 100.0
            if vol != 1.0:
                pcm = boost(pcm, vol)
            block = rate // 50  # 20 ms
            try:
                if out is None or out_rate != rate:
                    if out is not None:
                        out.close()
                    out = sd.OutputStream(samplerate=rate, channels=1, dtype="int16",
                                          device=self._speaker_device())
                    out.start()
                    out_rate = rate
                for i in range(0, len(pcm), block):
                    if self._stop.is_set():
                        out.abort()
                        out.close()
                        out = None
                        break
                    chunk = pcm[i:i + block]
                    self.level = level_of(chunk)
                    out.write(chunk)
            except Exception as e:  # noqa: BLE001 (a sound device problem shouldn't end Buddy)
                self.log(f"couldn't play sound: {e}")
                out = None
            self.level = 0.0
