"""Are you at the desk, facing him? One webcam frame, opened just for this
(Buddy's eyes uses the camera too), and a small face finder (YuNet, MIT
licence, in models/) that also marks the eyes and nose, so a face turned
away to talk to someone else doesn't count. Nothing is saved or sent anywhere."""

import os
import threading

MODEL = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx")
TURNED = 0.45  # nose this far off the middle of the eyes (in eye widths): looking away
_lock = threading.Lock()
_finder = None


def grab(index):
    """One frame (BGR) from the webcam, or None if it's busy or missing."""
    import cv2
    cam = cv2.VideoCapture(index, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(index)
    try:
        if not cam.isOpened():
            return None
        frame = None
        for _ in range(5):  # let the exposure settle a little
            ok, f = cam.read()
            if ok:
                frame = f
        return frame
    finally:
        cam.release()


def faces(frame):
    """[(x, y, w, h, how far turned, score)] for each face in the frame."""
    import cv2
    global _finder
    h, w = frame.shape[:2]
    if w > 640:
        frame = cv2.resize(frame, (640, int(h * 640 / w)))
        h, w = frame.shape[:2]
    with _lock:
        if _finder is None:
            _finder = cv2.FaceDetectorYN.create(MODEL, "", (w, h), 0.6, 0.3, 50)
        _finder.setInputSize((w, h))
        _, found = _finder.detect(frame)
    out = []
    for f in (found if found is not None else []):
        x, y, fw, fh = (float(v) for v in f[:4])
        rex, _, lex, _, nx = (float(v) for v in f[4:9])
        eyes = abs(lex - rex) or 1.0
        out.append((x, y, fw, fh, abs(nx - (rex + lex) / 2) / eyes, float(f[-1])))
    return out


def judge(frame):
    """'facing', 'turned' (there, looking elsewhere), 'away' (no face), or None (too dark to tell)."""
    import numpy as np
    if frame is None:
        return None
    if float(np.mean(frame)) < 25:
        return None  # the lights are off: the camera can't tell, so don't hold it against you
    found = faces(frame)
    if not found:
        return "away"
    return "facing" if any(t <= TURNED for *_, t, _ in found) else "turned"


def check(cfg, log):
    """What the camera sees of you right now (see judge); None if it can't tell."""
    try:
        result = judge(grab(cfg["camera_index"]))
    except Exception as e:  # noqa: BLE001 (no camera, no OpenCV face finder: don't block him)
        log(f"(couldn't check if you're facing me: {type(e).__name__}: {e})", debug=True)
        return None
    if result is None:
        log("(the camera can't tell if you're here just now: busy or too dark)", debug=True)
    return result


class Look:
    """Starts a check now, in the background; .result() waits for it (a few seconds at most)."""

    def __init__(self, cfg, log):
        self.value = None
        self._t = threading.Thread(target=lambda: setattr(self, "value", check(cfg, log)), daemon=True,
                                   name="presence")
        self._t.start()

    def result(self, timeout=3.0):
        self._t.join(timeout)
        return self.value if not self._t.is_alive() else None


WHY = {"away": "you're not at the desk", "turned": "you're turned away"}
