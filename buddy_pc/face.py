"""Buddy's animated face: two eyes and a mouth that tween smoothly between
expressions, blink on their own, glance around and "breathe".

A port of the desk Buddy's face engine (components/face/Face.cpp in
ecstoneusps/Claude-gadget), drawn with Pillow. It renders at twice the
window size and scales down, which smooths the edges.
"""

import math
import random
from dataclasses import dataclass, fields, replace

from PIL import Image, ImageDraw

EXPRESSIONS = (
    "neutral", "happy", "sad", "angry", "surprised", "sleepy", "confused", "listening", "love",
    "laughing", "crying", "embarrassed", "shocked", "thinking", "winking", "cool", "relaxed", "silly",
)

# The emotion tags the AI writes ([laughing], [loving]...) -> expressions.
EMOTION_ALIASES = {
    "funny": "laughing", "loving": "love", "kissy": "love", "delicious": "silly", "confident": "cool",
}


def expression_for(emotion):
    """The expression for an emotion tag, or None if it isn't one."""
    e = (emotion or "").strip().lower().replace(" ", "")
    e = EMOTION_ALIASES.get(e, e)
    return e if e in EXPRESSIONS else None


@dataclass
class FaceParams:
    eyeW: float = 64
    eyeH: float = 84
    eyeScaleL: float = 1
    eyeScaleR: float = 1
    openness: float = 1
    lidAngry: float = 0
    lidSad: float = 0
    lidBottom: float = 0
    mouthW: float = 52
    mouthCurve: float = 0.3
    mouthOpen: float = 0
    blush: float = 0
    eyeR: float = 40
    eyeG: float = 220
    eyeB: float = 255
    lookY: float = 0
    lookX: float = 0
    opennessL: float = 1
    opennessR: float = 1
    lidTop: float = 0
    tears: float = 0
    tongue: float = 0
    mouthSkew: float = 0
    bounce: float = 0


_TWEAKS = {
    "neutral": {},
    "happy": dict(eyeW=70, eyeH=80, lidBottom=0.6, mouthW=66, mouthCurve=1.0, mouthOpen=0.45, blush=0.7,
                  eyeR=70, eyeG=235, eyeB=255),
    "sad": dict(eyeW=60, eyeH=72, lidSad=0.55, mouthW=46, mouthCurve=-0.8, eyeR=80, eyeG=140, eyeB=255,
                lookY=0.5),
    "angry": dict(eyeW=66, eyeH=66, lidAngry=0.75, mouthW=50, mouthCurve=-0.6, eyeR=255, eyeG=70, eyeB=40),
    "surprised": dict(eyeW=72, eyeH=98, mouthW=30, mouthCurve=0, mouthOpen=1.0, eyeR=60, eyeG=235, eyeB=255,
                      lookY=-0.2),
    "sleepy": dict(eyeW=70, eyeH=84, openness=0.16, lidSad=0.15, mouthW=34, mouthCurve=0.1, mouthOpen=0.12,
                   eyeR=20, eyeG=120, eyeB=170, lookY=0.6),
    "confused": dict(eyeScaleL=1.12, eyeScaleR=0.72, mouthW=40, mouthCurve=-0.3, eyeR=120, eyeG=210, eyeB=255,
                     lookY=-0.3),
    "listening": dict(eyeW=70, eyeH=94, mouthW=28, mouthCurve=0.3, mouthOpen=0.2, eyeR=0, eyeG=255, eyeB=200,
                      lookY=-0.15),
    "love": dict(eyeW=70, eyeH=80, lidBottom=0.5, mouthW=56, mouthCurve=1.0, mouthOpen=0.25, blush=1.0,
                 eyeR=255, eyeG=90, eyeB=170),
    "laughing": dict(eyeW=72, eyeH=76, lidBottom=0.8, mouthW=76, mouthCurve=1.0, mouthOpen=0.85, blush=0.8,
                     eyeR=90, eyeG=240, eyeB=255, bounce=1.0),
    "crying": dict(eyeW=60, eyeH=70, lidSad=0.65, mouthW=50, mouthCurve=-1.0, mouthOpen=0.3, eyeR=70, eyeG=120,
                   eyeB=255, lookY=0.4, tears=1.0),
    "embarrassed": dict(eyeW=62, eyeH=72, lidBottom=0.3, mouthW=36, mouthCurve=0.5, mouthSkew=0.4, blush=1.0,
                        eyeR=255, eyeG=150, eyeB=190, lookY=0.55, lookX=-0.45),
    "shocked": dict(eyeW=80, eyeH=104, mouthW=34, mouthCurve=0, mouthOpen=1.0, eyeR=200, eyeG=245, eyeB=255,
                    lookY=-0.25),
    "thinking": dict(eyeW=64, eyeH=80, eyeScaleL=0.92, lidTop=0.15, mouthW=30, mouthCurve=-0.1, mouthSkew=0.8,
                     eyeR=120, eyeG=200, eyeB=255, lookY=-0.7, lookX=0.55),
    "winking": dict(eyeW=68, eyeH=84, opennessR=0.0, mouthW=54, mouthCurve=0.9, mouthSkew=-0.5, blush=0.4,
                    eyeR=60, eyeG=235, eyeB=255),
    "cool": dict(eyeW=74, eyeH=70, lidTop=0.55, mouthW=46, mouthCurve=0.5, mouthSkew=0.7, eyeR=150, eyeG=120,
                 eyeB=255),
    "relaxed": dict(eyeW=70, eyeH=78, lidTop=0.4, lidBottom=0.25, mouthW=50, mouthCurve=0.6, blush=0.3,
                    eyeR=80, eyeG=230, eyeB=210, lookY=-0.1),
    "silly": dict(eyeW=66, eyeH=84, eyeScaleL=1.15, eyeScaleR=0.78, mouthW=60, mouthCurve=0.9, mouthSkew=-0.3,
                  tongue=1.0, eyeR=120, eyeG=255, eyeB=160, lookX=-0.2),
}

_NAMES = [f.name for f in fields(FaceParams)]
BLINK_MS = 150.0
BREATH_MS = 3800.0


def params_for(expression):
    return replace(FaceParams(), **_TWEAKS.get(expression, {}))


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _approach(cur, target, dt, tau):
    return cur + (target - cur) * (1.0 - math.exp(-dt / tau))


class Face:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.expression = "neutral"
        self.cur = params_for("neutral")
        self.target = params_for("neutral")
        self.blink_timer = 2000.0
        self.blink_phase = -1.0
        self.double_blink = False
        self.look_x = self.look_y = self.look_tx = self.look_ty = 0.0
        self.gaze_timer = 1500.0
        self.talk = 0.0
        self.talk_target = 0.0
        self.time_ms = 0.0

    def set_expression(self, expression):
        if expression not in EXPRESSIONS:
            expression = "neutral"
        if expression != self.expression:
            self.expression = expression
            self.target = params_for(expression)

    def set_talk_level(self, level):
        self.talk_target = _clamp(level, 0.0, 1.0)

    def blink_now(self):
        if self.blink_phase < 0:
            self.blink_phase = 0.0

    def update(self, dt):
        """Advance the animation by dt milliseconds."""
        self.time_ms += dt
        for n in _NAMES:
            setattr(self.cur, n, _approach(getattr(self.cur, n), getattr(self.target, n), dt, 90.0))
        self.talk = _approach(self.talk, self.talk_target, dt, 35.0)

        if self.blink_phase >= 0:
            self.blink_phase += dt
            if self.blink_phase >= BLINK_MS:
                self.blink_phase = -1.0
                if self.double_blink:
                    self.double_blink = False
                    self.blink_timer = 90.0
                else:
                    sleepy = self.expression == "sleepy"
                    self.blink_timer = self.rng.uniform(3000, 7000) if sleepy else self.rng.uniform(1800, 5500)
                    self.double_blink = self.rng.random() < 0.18
        else:
            self.blink_timer -= dt
            if self.blink_timer <= 0:
                self.blink_phase = 0.0

        self.gaze_timer -= dt
        if self.gaze_timer <= 0:
            rng = 0.3 if self.expression == "sleepy" else 0.25 if self.expression == "listening" else 0.9
            if self.rng.random() < 0.45:  # drift back toward the middle
                self.look_tx = self.rng.uniform(-0.15, 0.15)
                self.look_ty = self.rng.uniform(-0.1, 0.1)
            else:
                self.look_tx = self.rng.uniform(-rng, rng)
                self.look_ty = self.rng.uniform(-rng * 0.6, rng * 0.6)
            self.gaze_timer = self.rng.uniform(700, 3500)
            if self.rng.random() < 0.25:
                self.blink_now()
        self.look_x = _approach(self.look_x, self.look_tx, dt, 55.0)
        self.look_y = _approach(self.look_y, self.look_ty, dt, 55.0)

    def render(self, width, height, supersample=2):
        """The face as a Pillow image of the given size."""
        W, H = width * supersample, height * supersample
        img = Image.new("RGB", (W, H), (0, 0, 0))
        self._draw(ImageDraw.Draw(img), W, H)
        if supersample > 1:
            img = img.resize((width, height), Image.LANCZOS)
        return img

    def _draw(self, d, W, H):
        p = self.cur
        s = min(W / 320.0, H / 240.0)
        bg = (0, 0, 0)
        er, eg, eb = int(p.eyeR), int(p.eyeG), int(p.eyeB)
        eye = (er, eg, eb)
        shine = (er + (255 - er) * 7 // 10, eg + (255 - eg) * 7 // 10, eb + (255 - eb) * 7 // 10)

        def ellipse(cx, cy, rx, ry, col):
            d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=col)

        blink = 1.0
        if self.blink_phase >= 0:
            blink = abs(self.blink_phase - BLINK_MS / 2) / (BLINK_MS / 2)
        open_both = _clamp(p.openness * blink, 0, 1)
        t = self.time_ms
        breath = (math.sin(t * 2 * math.pi / BREATH_MS) * 2.5 * s
                  + abs(math.sin(t * 2 * math.pi / 360.0)) * -5.0 * s * _clamp(p.bounce, 0, 1))
        gx = _clamp(self.look_x + p.lookX, -1, 1) * 24 * s
        gy = _clamp(self.look_y + p.lookY, -1, 1) * 16 * s

        cx = W * 0.5
        eye_y = H * 0.42 + breath + gy
        eye_dx = 74 * s

        for side in (-1, 1):
            persp = 1.0 + self.look_x * 0.08 * side
            sc = (p.eyeScaleL if side < 0 else p.eyeScaleR) * persp
            op = _clamp(open_both * (p.opennessL if side < 0 else p.opennessR), 0, 1)
            w = p.eyeW * s * sc
            h_full = p.eyeH * s * sc
            h = max(h_full * op, 5 * s)
            x = cx + side * eye_dx + gx
            top = eye_y - h / 2
            left = x - w / 2
            r = min(w, h) * 0.3
            d.rounded_rectangle((left, top, left + w, top + h), radius=r, fill=eye)

            if op > 0.55 and p.lidBottom < 0.45 and p.lidTop < 0.2:
                hr = w * 0.1
                ellipse(x - w * 0.2, top + h * 0.24, hr, hr, shine)
                ellipse(x - w * 0.02, top + h * 0.38, hr * 0.45, hr * 0.45, shine)

            inner = x - side * (w / 2 + 3 * s)
            outer = x + side * (w / 2 + 3 * s)
            if p.lidAngry > 0.01:
                d.polygon([(outer, top - 3 * s), (inner, top - 3 * s), (inner, top + p.lidAngry * h * 0.6)], fill=bg)
            if p.lidSad > 0.01:
                d.polygon([(inner, top - 3 * s), (outer, top - 3 * s), (outer, top + p.lidSad * h * 0.6)], fill=bg)
            if p.lidTop > 0.01:
                d.rectangle((left - 3 * s, top - 3 * s, left + w + 3 * s, top + p.lidTop * h * 0.55), fill=bg)
            if p.lidBottom > 0.01:
                ry = h * 0.6
                cy_lid = top + h + ry - p.lidBottom * h * 1.05
                ellipse(x, cy_lid, w * 0.6, ry, bg)

            if p.tears > 0.05:
                tt = _clamp(p.tears, 0, 1)
                tc = (int(90 * tt), int(170 * tt), int(255 * tt))
                for k in range(2):
                    phase = (t / 1100.0 + k * 0.5 + (0.25 if side > 0 else 0)) % 1.0
                    tx = x + side * w * 0.32
                    ty = top + h + 4 * s + phase * 46 * s
                    rr = 4.5 * s
                    ellipse(tx, ty, rr, rr, tc)
                    d.polygon([(tx - rr, ty), (tx + rr, ty), (tx, ty - 2.4 * rr)], fill=tc)

            if p.blush > 0.02:
                b = _clamp(p.blush, 0, 1) * 0.6
                ellipse(x + side * w * 0.45, eye_y + h_full * 0.58, w * 0.3, 9 * s,
                        (int(255 * b), int(70 * b), int(120 * b)))

        # Mouth: a thick curve, or an open "D" / "O" while talking or surprised.
        mx = cx + gx * 0.5 + p.mouthSkew * p.mouthW * 0.18 * s
        my = H * 0.78 + breath * 0.6 + gy * 0.35
        mw = p.mouthW * s
        k = p.mouthCurve * 13 * s
        thick = 3.6 * s
        m_open = _clamp(p.mouthOpen + self.talk * 0.8, 0, 1)
        if m_open <= 0.04:
            pts = []
            n = 28
            for i in range(n + 1):
                u = i / n * 2 - 1
                pts.append((mx + u * mw / 2, my - k * 0.5 + k * (1 - u * u) - p.mouthSkew * u * 7 * s))
            d.line(pts, fill=eye, width=max(1, int(thick * 2)), joint="curve")
            for px, py in (pts[0], pts[-1]):
                ellipse(px, py, thick, thick, eye)
        elif k > 3 * s:
            rx = mw * 0.5
            ry = 4 * s + m_open * 26 * s
            y0 = my - k * 0.4
            ellipse(mx, y0, rx, ry, eye)
            d.rectangle((mx - rx - 2 * s, y0 - ry - 2 * s, mx + rx + 2 * s, y0), fill=bg)
        else:
            rx = max(mw * 0.42, 9 * s) * (0.75 + 0.25 * m_open)
            ry = 3 * s + m_open * 18 * s
            ellipse(mx, my, rx, ry, eye)

        if p.tongue > 0.05:
            tt = _clamp(p.tongue, 0, 1)
            tx = mx + mw * 0.12
            if m_open > 0.04 and k > 3 * s:
                ty = my - k * 0.4 + (4 + m_open * 26) * s * 0.9
            else:
                ty = my + k * 0.5 + 6 * s
            ellipse(tx, ty, mw * 0.17 * tt, 11 * s * tt, (int(255 * tt), int(95 * tt), int(140 * tt)))
