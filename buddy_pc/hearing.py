"""Hearing: the microphone, the wake word (openWakeWord), recording what you
say until you stop, and turning it into text (faster-whisper, on the
graphics card when it can)."""

import glob
import os
import queue
import sys
import threading
import time

import numpy as np

RATE = 16000
FRAME = 1280  # 80 ms: what openWakeWord wants per call


def _db(frame):
    rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) / 32768.0
    return 20 * np.log10(rms) if rms > 1e-6 else -120.0


class Mic:
    """The microphone as a queue of 80 ms int16 frames."""

    def __init__(self, device=None):
        import sounddevice as sd
        self.frames = queue.Queue(maxsize=200)
        info = sd.query_devices(device or None, "input")
        self.name = info["name"]
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                     device=device or None, callback=self._cb)
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        try:
            self.frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def drain(self):
        try:
            while True:
                self.frames.get_nowait()
        except queue.Empty:
            pass

    def read(self, timeout=1.0):
        return self.frames.get(timeout=timeout)


class Endpointer:
    """Decides when you started and stopped talking, from loudness compared
    with the room's background level (which it keeps learning)."""

    def __init__(self, start_window=6.0, end_silence=0.9, max_len=20.0):
        self.start_window, self.end_silence, self.max_len = start_window, end_silence, max_len
        self.floor = -60.0
        self.loudest = -120.0  # in the last recording, for "I couldn't hear you" messages

    def learn(self, frame):
        db = _db(frame)
        # Follow the background quickly down, slowly up (speech isn't background).
        self.floor += (db - self.floor) * (0.3 if db < self.floor else 0.02)

    def is_speech(self, frame):
        db = _db(frame)
        return db > max(self.floor + 12.0, -52.0)

    def record(self, mic, preroll=(), cancelled=lambda: False):
        """Frames from when you start talking until you stop, or None if you
        didn't say anything (or it was cancelled)."""
        frames = list(preroll)
        heard = False
        self.loudest = -120.0
        t0 = last = time.time()
        while True:
            if cancelled():
                return None
            try:
                f = mic.read()
            except queue.Empty:
                continue
            frames.append(f)
            now = time.time()
            self.loudest = max(self.loudest, _db(f))
            if self.is_speech(f):
                heard = True
                last = now
            elif not heard:
                self.learn(f)
                frames = frames[-4:]  # keep a little before you start
            if not heard and now - t0 > self.start_window:
                return None
            if heard and now - last > self.end_silence:
                return np.concatenate(frames)
            if now - t0 > self.max_len:
                return np.concatenate(frames) if heard else None


class WakeWord:
    """'Hey Jarvis' (built in) until a 'Hey Buddy' model is trained."""

    def __init__(self, model, threshold, log):
        self.threshold = threshold
        self.log = log
        import openwakeword.utils
        from openwakeword.model import Model
        if model.lower().endswith((".onnx", ".tflite")):
            paths = [model]
        else:
            openwakeword.utils.download_models([model])
            paths = [model]
        self.model = Model(wakeword_models=paths, inference_framework="onnx")
        self.name = model

    def heard(self, frame):
        scores = self.model.predict(frame)
        return max(scores.values()) >= self.threshold if scores else False

    def reset(self):
        self.model.reset()


# ---------------------------------------------------------------------------
# Whisper

def _add_cuda_dlls():
    """faster-whisper on Windows needs the cuBLAS / cuDNN DLLs from the
    nvidia-* pip packages on the DLL search path."""
    if os.name != "nt":
        return
    for base in sys.path:
        for d in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log
        _add_cuda_dlls()
        from faster_whisper import WhisperModel
        self.model = None
        tries = []
        if cfg["whisper_device"] in ("auto", "cuda"):
            tries.append(("cuda", "float16", cfg["whisper_model"]))
        if cfg["whisper_device"] in ("auto", "cpu"):
            # On the processor the big model is slow: a smaller one keeps it snappy.
            tries.append(("cpu", "int8", cfg["whisper_model"] if cfg["whisper_device"] == "cpu" else "small"))
        for device, compute, name in tries:
            try:
                t0 = time.time()
                m = WhisperModel(name, device=device, compute_type=compute)
                list(m.transcribe(np.zeros(RATE, dtype=np.float32), language=cfg["language"] or None)[0])
                self.model, self.device = m, device
                log(f"Whisper {name} ready on the {'graphics card' if device == 'cuda' else 'processor'} "
                    f"({time.time() - t0:.1f} s)")
                break
            except Exception as e:  # noqa: BLE001 (try the next way)
                log(f"Whisper on {device} didn't work: {e}")
        if self.model is None:
            raise RuntimeError("Whisper couldn't start")

    def text(self, pcm):
        audio = pcm.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(audio, language=self.cfg["language"] or None, beam_size=1,
                                            condition_on_previous_text=False, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()
