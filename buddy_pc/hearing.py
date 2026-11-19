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


def find_device(name, kind, devices=None, hostapis=None):
    """The device index whose name contains `name` (any case), or None for
    Windows' default. Windows lists each device once per audio system
    (MME, DirectSound, WASAPI...); the MME one is used, since it accepts any
    sample rate. `kind` is "input" or "output"."""
    if not name:
        return None
    if devices is None:
        import sounddevice as sd
        devices, hostapis = sd.query_devices(), sd.query_hostapis()
    channels = "max_input_channels" if kind == "input" else "max_output_channels"
    order = ["MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS"]
    matches = [(i, d) for i, d in enumerate(devices) if d[channels] > 0 and name.lower() in d["name"].lower()]
    if not matches:
        raise ValueError(f"no {kind} device has \"{name}\" in its name (see list-devices.bat)")
    api = lambda d: hostapis[d["hostapi"]]["name"] if hostapis else ""
    matches.sort(key=lambda m: order.index(api(m[1])) if api(m[1]) in order else len(order))
    return matches[0][0]


def voice_detector(log):
    """Silero's voice detector from openWakeWord, or None if it isn't there."""
    try:
        import openwakeword.utils
        from openwakeword.vad import VAD
        openwakeword.utils.download_models(["silero_vad"])
        return VAD()
    except Exception as e:  # noqa: BLE001
        log(f"voice detector off ({e}); going by loudness alone")
        return None


class Mic:
    """The microphone as a queue of 80 ms int16 frames."""

    def __init__(self, device=None):
        import sounddevice as sd
        self.frames = queue.Queue(maxsize=200)
        device = find_device(device, "input")
        info = sd.query_devices(device, "input")
        self.name = info["name"]
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                     device=device, callback=self._cb)
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

    def __init__(self, start_window=6.0, end_silence=0.9, max_len=20.0, vad=None):
        self.start_window, self.end_silence, self.max_len = start_window, end_silence, max_len
        self.floor = -60.0
        self.loudest = -120.0  # in the last recording, for "I couldn't hear you" messages
        # A voice detector (Silero, via openWakeWord): with it, only voices
        # count as talking, not a fan, typing or music getting louder.
        self.vad = vad
        self.hit_limit = False

    def learn(self, frame):
        db = _db(frame)
        # Follow the background quickly down, slowly up (speech isn't background).
        self.floor += (db - self.floor) * (0.3 if db < self.floor else 0.02)

    def is_speech(self, frame):
        db = _db(frame)
        if db <= max(self.floor + 12.0, -52.0):
            return False
        if self.vad is None:
            return True
        try:
            return self.vad.predict(frame, frame_size=640) > 0.5
        except Exception:  # noqa: BLE001 (the loudness alone, then)
            self.vad = None
            return True

    def record(self, mic, preroll=(), cancelled=lambda: False, ignore_first=0.0):
        """Frames from when you start talking until you stop, or None if you
        didn't say anything (or it was cancelled). The first `ignore_first`
        seconds are skipped: right after he talks, that's his own echo."""
        frames = list(preroll)
        heard = False
        self.loudest = -120.0
        self.hit_limit = False
        if self.vad is not None:
            self.vad.reset_states()
        t0 = last = time.time()
        while True:
            if cancelled():
                return None
            try:
                f = mic.read()
            except queue.Empty:
                continue
            now = time.time()
            if now - t0 < ignore_first:
                continue
            frames.append(f)
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
                self.hit_limit = True
                return np.concatenate(frames) if heard else None


class WakeWord:
    """'Hey Jarvis' (built in) until a 'Hey Buddy' model is trained."""

    def __init__(self, model, threshold, log):
        self.threshold = threshold
        self.log = log
        import openwakeword.utils
        from openwakeword.model import Model
        if model.lower().endswith((".onnx", ".tflite")):
            # A trained model file (e.g. hey_buddy.onnx), next to run-buddy.bat
            # unless a full path is given. It still needs the shared feature models.
            from .config import HERE
            path = model if os.path.isabs(model) else os.path.join(HERE, model)
            if not os.path.isfile(path):
                raise FileNotFoundError(f"{path} isn't there")
            if path.lower().endswith(".tflite"):
                raise ValueError("use the .onnx version of the model (the .tflite one needs Linux)")
            openwakeword.utils.download_models(["silero_vad"])
            paths = [path]
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
# A wake word without training: a tiny Whisper model checks the start of
# anything said for the phrase ("hey buddy"). It only runs when a voice is
# heard, takes a fraction of a second on the processor, and copes with
# "hey buddy" heard as "hey body" or "heybuddy".

OWW_MODELS = {"hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy", "ok_nabu", "timer", "weather"}


def is_phrase_wake_word(name):
    """'hey buddy' (a phrase for PhraseWake) rather than an openWakeWord model."""
    n = name.strip().lower()
    return bool(n) and n not in OWW_MODELS and not n.endswith((".onnx", ".tflite")) and n not in ("off", "none")


def match_phrase(phrase, text):
    """Where the phrase ends in `text` if it's said near the start (within
    the first few words), allowing for small mishearings; else -1. The result
    counts words of normalize(text)."""
    from difflib import SequenceMatcher
    from .textutil import normalize
    want = normalize(phrase.replace("_", " "))
    n = len(want.split())
    words = normalize(text).split()
    want_words = want.split()
    same = {"hey": {"hi", "hay", "heh", "a", "okay"}, "ok": {"okay"}, "okay": {"ok"}}
    sim = lambda a, b: 1.0 if a == b or a in same.get(b, ()) else SequenceMatcher(None, a, b).ratio()
    for start in range(0, min(3, len(words))):
        # Word by word ("hey body": each word close enough, and close overall).
        got = words[start:start + n]
        if len(got) == n:
            scores = [sim(a, b) for a, b in zip(got, want_words)]
            if min(scores) >= 0.6 and sum(scores) / n >= 0.75:
                return start + n
        # Run together ("heybuddy").
        if n > 1 and start < len(words) and sim(words[start], want.replace(" ", "")) >= 0.85:
            return start + 1
    return -1


def strip_phrase(phrase, text):
    """'Hey Buddy, what time is it?' -> 'what time is it?' ("" if only the phrase)."""
    end = match_phrase(phrase, text)
    if end < 0:
        return text
    tokens = text.split()
    from .textutil import normalize
    count, i = 0, 0
    while i < len(tokens) and count < end:  # skip the tokens holding the first `end` words
        count += len(normalize(tokens[i]).split())
        i += 1
    return " ".join(tokens[i:]).lstrip(" ,.!?-").strip()


class PhraseWake:
    def __init__(self, phrase, log):
        from faster_whisper import WhisperModel
        self.phrase = phrase.replace("_", " ").strip()
        self.model = WhisperModel("tiny.en", device="cpu", compute_type="int8", cpu_threads=2)

    def check(self, pcm):
        """(heard the phrase?, what tiny Whisper heard)."""
        audio = pcm.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1, condition_on_previous_text=False,
                                            without_timestamps=True, max_new_tokens=30)
        text = " ".join(s.text.strip() for s in segments).strip()
        return match_phrase(self.phrase, text) >= 0, text

    def reset(self):
        pass


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
                # On the processor, half the cores (the default uses only 4).
                threads = max(4, (os.cpu_count() or 8) // 2) if device == "cpu" else 0
                m = WhisperModel(name, device=device, compute_type=compute, cpu_threads=threads)
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
        # No timestamps and a cap on its length: noise can make Whisper ramble
        # on for seconds (only to be thrown away as a hallucination).
        segments, _ = self.model.transcribe(audio, language=self.cfg["language"] or None, beam_size=1,
                                            condition_on_previous_text=False, vad_filter=True,
                                            without_timestamps=True, max_new_tokens=100)
        return " ".join(s.text.strip() for s in segments if not hallucination(s)).strip()


# What Whisper "hears" in noise, an echo or silence (it was trained on a lot
# of videos ending "thank you for watching").
HALLUCINATIONS = {"thank you", "thanks", "thank you very much", "thank you so much", "thanks for watching",
                  "thank you for watching", "thanks for listening", "you", "bye", "bye bye", "okay", "oh",
                  "please subscribe", "subtitles by the amaraorg community", "so", "um", "hmm"}


def hallucination(seg):
    """True for a segment that's probably not real speech."""
    from .textutil import normalize
    if seg.no_speech_prob > 0.6 or seg.avg_logprob < -1.0:
        return True
    if normalize(seg.text) in HALLUCINATIONS and (seg.no_speech_prob > 0.15 or seg.avg_logprob < -0.5):
        return True
    return False
