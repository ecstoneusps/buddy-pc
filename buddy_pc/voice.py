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


TARGET_DB = -16.0  # how loud speech is played at volume 100 (dBFS, average while talking)


def rms_db(pcm):
    """Average loudness while talking (silences left out), in dBFS."""
    x = pcm.astype(np.float32) / 32768.0
    if len(x) == 0:
        return -120.0
    frames = x[: len(x) // 480 * 480].reshape(-1, 480) if len(x) >= 480 else x.reshape(1, -1)
    levels = np.sqrt(np.mean(frames ** 2, axis=1))
    loud = levels[levels > max(1e-4, levels.max() * 0.1)]  # the talking parts
    r = float(np.sqrt(np.mean(loud ** 2))) if len(loud) else 0.0
    return 20 * np.log10(r) if r > 1e-6 else -120.0


def level_gain(pcm, volume=1.0, most=12.0):
    """The gain that brings this speech to TARGET_DB (times `volume`), at most
    `most`; and how loud it was before."""
    before = rms_db(pcm)
    if before <= -100:
        return volume, before
    gain = 10 ** ((TARGET_DB - before) / 20) * volume
    return max(0.1, min(most, gain)), before


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
        self.told = False
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
            mine = {os.getpid(), os.getppid()}  # (on Windows, Python may run under a launcher process)
            names = []
            for s in AudioUtilities.GetAllSessions():
                p = s.Process
                if p is None:
                    continue  # system sounds
                if p.pid in mine or p.name().lower().startswith("python"):
                    # Himself: always at full volume in Windows' mixer (it remembers
                    # an app's level between runs, and his once got stuck at 1%).
                    if s.SimpleAudioVolume.GetMasterVolume() < 0.99:
                        s.SimpleAudioVolume.SetMasterVolume(1.0, None)
                        self.log("(his own volume in Windows' mixer was turned down: back to 100%)")
                    continue
                vol = s.SimpleAudioVolume
                was = vol.GetMasterVolume()
                vol.SetMasterVolume(was * self.level, None)
                self.saved[s] = (vol, was)
                names.append(p.name())
            if names and not self.told:
                self.log(f"(turning down while he talks: {', '.join(sorted(set(names)))})")
                self.told = True
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
        self.told_level = False
        self.busy = False         # something queued or playing
        self._texts = queue.Queue()
        self._audio = queue.Queue(maxsize=3)
        self._stop = threading.Event()
        self._done = threading.Event()
        self._done.set()
        threading.Thread(target=self._synth_loop, daemon=True, name="tts").start()
        threading.Thread(target=self._play_loop, daemon=True, name="play").start()

    def chime(self):
        """A short rising two-note "I'm listening", at his volume, finished before
        he starts listening (so he doesn't hear it as you)."""
        try:
            import sounddevice as sd
            rate = 24000
            notes = []
            for f, ms in ((880, 70), (1320, 110)):
                t = np.arange(int(rate * ms / 1000)) / rate
                env = np.minimum(1.0, np.minimum(t, t[::-1]) * 200)  # 5 ms fade in and out: no clicks
                notes.append(np.sin(2 * np.pi * f * t) * env)
            wave = np.concatenate(notes) * 0.35 * min(2.0, self.cfg["volume"] / 100.0)
            sd.play(np.clip(wave * 32767, -32768, 32767).astype(np.int16), rate,
                    device=self._speaker_device(), blocking=True)
        except Exception as e:  # noqa: BLE001 (no chime then)
            self.log(f"(no chime: {e})", debug=True)

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
            # Every sentence brought to the same loudness (Kokoro's can come out
            # quiet), then `volume` on top; the limiter keeps it from clipping.
            gain, before = level_gain(pcm, self.cfg["volume"] / 100.0)
            pcm = boost(pcm, gain)
            if not self.told_level:
                self.log(f"(his voice came at {before:.0f} dB average; played at about "
                         f"{rms_db(pcm):.0f} dB, gain x{gain:.1f})")
                self.told_level = True
            block = rate // 50  # 20 ms
            try:
                if out is None or out_rate != rate:
                    if out is not None:
                        out.close()
                    out = sd.OutputStream(samplerate=rate, channels=1, dtype="int16",
                                          device=self._speaker_device())
                    out.start()
                    out_rate = rate
                # (After his sound has started, so Windows lists him too and his
                # own level can be checked; once per reply.)
                ducker.duck()
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
