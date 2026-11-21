"""What the PC itself is playing (Windows "loopback" of the speakers), kept
for the last few seconds. If what the microphone heard is what the PC just
played (a video, a game, a call), it wasn't you."""

import collections
import re
import threading
import time

import numpy as np

RATE = 16000


class Loopback:
    def __init__(self, log, seconds=15):
        self.log = log
        self.ok = False
        self._buf = collections.deque()  # (time it ended, int16 chunk)
        self._keep = seconds
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="loopback").start()

    def _loop(self):
        try:
            import soundcard as sc
            speaker = sc.default_speaker()
            source = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception as e:  # noqa: BLE001 (not Windows, or not installed)
            self.log(f"(can't hear what the PC plays: {type(e).__name__}: {e}; going by voice and loudness)")
            return
        self.log(f"(listening to what the PC plays on \"{speaker.name}\", to tell it apart from you)")
        while True:
            try:
                with source.recorder(samplerate=RATE, channels=1, blocksize=RATE // 10) as rec:
                    self.ok = True
                    while True:
                        data = rec.record(numframes=RATE // 10)  # 0.1 s
                        chunk = np.clip(data[:, 0] * 32767, -32768, 32767).astype(np.int16)
                        now = time.time()
                        with self._lock:
                            self._buf.append((now, chunk))
                            while self._buf and now - self._buf[0][0] > self._keep:
                                self._buf.popleft()
            except Exception as e:  # noqa: BLE001 (the speakers changed, say): start again
                self.ok = False
                self.log(f"(listening to the PC's sound stopped: {e}; trying again)", debug=True)
                time.sleep(5)

    def recent(self, seconds):
        """The last `seconds` the PC played, as int16 audio (b"" if none)."""
        cut = time.time() - seconds
        with self._lock:
            parts = [c for t, c in self._buf if t >= cut]
        return np.concatenate(parts) if parts else np.zeros(0, np.int16)


_SMALL = set("a an the and or but so to of in on at it is are was i you he she we they me my your this that "
             "what do does did not no yes oh okay ok um uh".split())


def _words(text):
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in _SMALL and len(w) > 1]


def same_words(heard, played):
    """True if what the mic heard is mostly what the PC played: most of its
    words (at least two) are among the PC's."""
    h, p = _words(heard), set(_words(played))
    if len(h) < 2:
        return False
    return sum(w in p for w in h) / len(h) >= 0.6


def is_quiet(pcm):
    return len(pcm) == 0 or float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) < 100  # about -50 dB
