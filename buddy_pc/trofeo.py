"""His face on a Thermalright Trofeo Vision 9.16" screen (USB 0416:5408).

The screen takes 1920x462 JPEG pictures over USB, split into 512-byte
pieces (a 16-byte header and 496 bytes of picture each). It goes back to its
logo 2-3 seconds after the last picture, so pictures keep coming. How it
works was worked out by the trcc-linux project
(github.com/Lexonight1/thermalright-trcc-linux); this is a small separate
implementation of it. Thermalright's own TRCC app must be closed: only one
program can use the screen at a time.
"""

import io
import struct
import threading
import time

VID, PID = 0x0416, 0x5408
WIDTH, HEIGHT = 1920, 462
EP_OUT, EP_IN = 0x01, 0x81
CHUNK, CHUNK_HEADER, CHUNK_DATA = 512, 16, 496
MAX_JPEG = 450000  # the screen ignores bigger pictures


def handshake_packet():
    return bytes([0x02, 0xFF, 0, 0, 0, 0, 0, 0, 0x01, 0, 0, 0, 0, 0, 0, 0]) + bytes(2032)


def parse_handshake(resp):
    """The SUB byte from the screen's reply (it decides which way up pictures
    go), or raises ValueError if it isn't the reply expected."""
    if len(resp) < 37 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1:
        raise ValueError(f"unexpected reply from the screen: {bytes(resp[:16]).hex(' ')}")
    return resp[22] + 1


def angle_for(sub, setting="auto"):
    """Rotation before sending: 180 on most of these screens, 0 on some (SUB 2-4)."""
    if str(setting).strip() in ("0", "180"):
        return int(setting)
    return 0 if sub in (2, 3, 4) else 180


def frame_chunks(jpeg):
    """A JPEG as the screen's 512-byte pieces, padded to a multiple of four."""
    total = len(jpeg)
    count = total // CHUNK_DATA + 1
    padded = count + (-count % 4)
    out = bytearray(padded * CHUNK)
    for i in range(count):
        at = i * CHUNK
        data = jpeg[i * CHUNK_DATA:(i + 1) * CHUNK_DATA]
        out[at] = 0x01
        out[at + 1] = 0xFF
        struct.pack_into("<IH", out, at + 2, total, len(data))
        out[at + 8] = 0x01
        struct.pack_into("<HH", out, at + 9, count, i)
        out[at + CHUNK_HEADER:at + CHUNK_HEADER + len(data)] = data
    return bytes(out)


def usb_writes(frame):
    """How the pieces go out: 4096 bytes at a time, the rest in one 2048-byte write."""
    pos = 0
    while pos < len(frame):
        size = 4096 if len(frame) - pos >= 4096 else min(2048, len(frame) - pos)
        yield frame[pos:pos + size]
        pos += size


class TrofeoScreen:
    def __init__(self, log, rotate="auto"):
        import usb.core
        try:
            import libusb_package  # the libusb DLL, on Windows
            backend = libusb_package.get_libusb1_backend()
        except ImportError:
            backend = None
        self.log = log
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
        if dev is None:
            raise RuntimeError("no Trofeo Vision screen found (is it plugged in?)")
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # already configured (Windows)
        self.dev = dev
        try:
            self.dev.write(EP_OUT, handshake_packet(), timeout=1000)
            resp = bytes(self.dev.read(EP_IN, 512, timeout=1000))
        except usb.core.USBError as e:
            raise RuntimeError(f"the screen didn't answer ({e}); is Thermalright's TRCC app still running? "
                               "Close it (also from the tray) and try again") from e
        sub = parse_handshake(resp)
        self.angle = angle_for(sub, rotate)
        log(f"Trofeo Vision screen ready (SUB {sub}, pictures turned {self.angle}°)")

    def send_jpeg(self, jpeg):
        import usb.core
        if len(jpeg) >= MAX_JPEG:
            return False
        for part in usb_writes(frame_chunks(jpeg)):
            self.dev.write(EP_OUT, part, timeout=5000)
        try:
            self.dev.read(EP_IN, 512, timeout=1000)  # its "got it"
        except usb.core.USBError:
            pass
        return True

    def show(self, image, quality=85):
        """Shows a 1920x462 Pillow image."""
        if self.angle:
            image = image.rotate(self.angle)
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=quality)
        return self.send_jpeg(buf.getvalue())


def run(face, cfg, log, stop=None):
    """Keeps the screen showing the face (a thread). Reconnects if the screen
    goes away (unplugged, or TRCC took it)."""
    from PIL import Image
    stop = stop or threading.Event()
    fps = max(2, min(30, cfg.get("trofeo_fps", 20)))
    screen, warned = None, False
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    # The face is drawn 4:3 in the middle; the sides stay black.
    fw = HEIGHT * 4 // 3
    left = (WIDTH - fw) // 2
    shown, t_report = 0, time.time()
    while not stop.is_set():
        if screen is None:
            try:
                screen = TrofeoScreen(log, cfg.get("trofeo_rotate", "auto"))
                warned = False
            except Exception as e:  # noqa: BLE001 (not there, or busy: try again later)
                if not warned:
                    log(f"Trofeo screen: {e}")
                    warned = True
                stop.wait(5)
                continue
        t0 = time.time()
        try:
            canvas.paste(face.render(fw, HEIGHT), (left, 0))
            screen.show(canvas)
            shown += 1
        except Exception as e:  # noqa: BLE001
            log(f"Trofeo screen stopped answering ({e}); trying again")
            screen = None
            continue
        if time.time() - t_report > 60:
            log(f"(Trofeo screen: {shown / (time.time() - t_report):.1f} pictures a second)", debug=True)
            shown, t_report = 0, time.time()
        stop.wait(max(0.0, 1.0 / fps - (time.time() - t0)))
