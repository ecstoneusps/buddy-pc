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


def angle_for(sub, setting="auto", portrait=False):
    """How far to turn a picture (counter-clockwise) before sending it: the
    panel itself wants 180 on most of these screens (0 on SUB 2-4), plus a
    quarter turn when it stands upright. trofeo_rotate (0/90/180/270)
    overrides it all, for a screen that comes out upside down."""
    if str(setting).strip() in ("0", "90", "180", "270"):
        return int(setting)
    base = 0 if sub in (2, 3, 4) else 180
    return (base + 90) % 360 if portrait else base


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


def endpoint_pairs(dev):
    """Every (bulk OUT, bulk IN) endpoint pair the screen offers, from its own
    description. It has more than one, and Windows doesn't always list them
    in the same order (after a restart it was 0x01 first, before 0x09), so
    each is tried until the screen answers."""
    pairs = []
    try:
        cfg = dev.get_active_configuration()
        for intf in cfg:
            eps = list(intf)
            outs = [e.bEndpointAddress for e in eps if (e.bmAttributes & 3) == 2 and not e.bEndpointAddress & 0x80]
            ins = [e.bEndpointAddress for e in eps if (e.bmAttributes & 3) == 2 and e.bEndpointAddress & 0x80]
            pairs += [(o, i) for o in outs for i in ins]
    except Exception:  # noqa: BLE001
        pass
    # The ones seen to work first (0x09 on Windows, 0x01 in trcc-linux).
    known = [(0x09, EP_IN), (EP_OUT, EP_IN)]
    ordered = [p for p in known if p in pairs] + [p for p in pairs if p not in known]
    return ordered or known


class TrofeoScreen:
    def __init__(self, log, rotate="auto", portrait=False):
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
        resp, tried, last = None, [], None
        for out_ep, in_ep in endpoint_pairs(dev):
            tried.append(f"0x{out_ep:02x}")
            try:
                dev.write(out_ep, handshake_packet(), timeout=1000)
                r = bytes(dev.read(in_ep, 512, timeout=1000))
                parse_handshake(r)
            except (usb.core.USBError, ValueError) as e:
                last = e
                continue
            self.ep_out, self.ep_in, resp = out_ep, in_ep, r
            break
        if resp is None:
            raise RuntimeError(f"the screen didn't answer on any of its endpoints ({', '.join(tried)}: {last}); "
                               "is another program using it (Thermalright's TRCC app, SignalRGB)?")
        sub = parse_handshake(resp)
        self.angle = angle_for(sub, rotate, portrait)
        log(f"Trofeo Vision screen ready (endpoint 0x{self.ep_out:02x}, SUB {sub}, pictures turned {self.angle}°)")

    def send_jpeg(self, jpeg):
        import usb.core
        if len(jpeg) >= MAX_JPEG:
            return False
        for part in usb_writes(frame_chunks(jpeg)):
            self.dev.write(self.ep_out, part, timeout=5000)
        try:
            self.dev.read(self.ep_in, 512, timeout=1000)  # its "got it"
        except usb.core.USBError:
            pass
        return True

    def show(self, image, quality=85):
        """Shows a Pillow image: 1920x462 (landscape) or 462x1920 (portrait)."""
        if self.angle:
            image = image.rotate(self.angle, expand=True)
        if image.size != (WIDTH, HEIGHT):
            image = image.resize((WIDTH, HEIGHT))
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=quality)
        return self.send_jpeg(buf.getvalue())


# ---------------------------------------------------------------------------
# What's on the screen: the time, his face, and the PC's vitals


class Stats:
    """CPU and GPU readings, refreshed every couple of seconds (a thread)."""

    def __init__(self, log):
        self.log = log
        self.cpu = self.mhz = self.gpu_temp = self.gpu_use = self.cpu_temp = self.fps = None
        self._said = ""
        threading.Thread(target=self._loop, daemon=True, name="stats").start()

    def _loop(self):
        import os
        import subprocess
        try:
            import psutil
        except ImportError:
            psutil = None
        hide = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashing up
        while True:
            # Everything is worked out first and swapped in at once, so a picture
            # drawn meanwhile never catches a reading half updated (it blinked).
            cpu = psutil.cpu_percent(interval=None) if psutil else None
            mhz = None  # (Windows only knows the base clock: only Afterburner's is shown)
            ab = read_afterburner()  # MSI Afterburner (with its CPU graphs on) has them all
            gpu_temp = gpu_use = cpu_temp = None
            if ab:
                cpu_temp = pick(ab, "CPU temperature")
                cpu = pick(ab, "CPU usage") if pick(ab, "CPU usage") is not None else cpu
                mhz = pick(ab, "CPU clock", "CPU1 clock")
                gpu_temp = pick(ab, "GPU temperature", "GPU1 temperature")
                gpu_use = pick(ab, "GPU usage", "GPU1 usage")
                if self._said != "ok":
                    names = ", ".join(sorted(ab)[:12])
                    self.log(f"Trofeo screen: readings from MSI Afterburner ({len(ab)} graphs: {names})")
                    self._said = "ok"
            elif self._said != AFTERBURNER_ERROR[0] and AFTERBURNER_ERROR[0]:
                self._said = AFTERBURNER_ERROR[0]
                self.log(f"Trofeo screen: {AFTERBURNER_ERROR[0]}; using Windows' and NVIDIA's readings")
            if gpu_temp is None or gpu_use is None:
                try:
                    out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu",
                                          "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                         timeout=5, creationflags=hide).stdout.split("\n")[0].split(",")
                    gpu_temp = float(out[0]) if gpu_temp is None else gpu_temp
                    gpu_use = float(out[1]) if gpu_use is None else gpu_use
                except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                    pass
            if cpu_temp is None:
                cpu_temp = _lhm_cpu_temp()
            fps = read_rtss_fps()
            if fps is None and ab:
                # Afterburner shares RivaTuner's frame rate too (0 when no game is running).
                fr = pick(ab, "Framerate")
                fps = fr if fr and 0 < fr < 2000 else None
            (self.cpu, self.mhz, self.cpu_temp, self.gpu_temp, self.gpu_use,
             self.fps) = (cpu, mhz, cpu_temp, gpu_temp, gpu_use, fps)
            time.sleep(1)


def parse_afterburner(buf):
    """MSI Afterburner's shared readings ("MAHMSharedMemory": a header, then
    one entry per graph): {name: value}, e.g. {"CPU temperature": 44.0}."""
    sig, version, header_size, count, entry_size = struct.unpack_from("<5I", buf, 0)
    if sig != 0x4D41484D or not header_size or not entry_size:  # "MAHM" (0xDEAD: Afterburner closed)
        return {}
    out = {}
    for i in range(min(count, 512)):
        at = header_size + i * entry_size
        if at + 1304 > len(buf):
            break
        name = bytes(buf[at:at + 260]).split(b"\0", 1)[0].decode("latin-1").strip()
        (value,) = struct.unpack_from("<f", buf, at + 1300)  # after five 260-character strings
        if name and name not in out:
            out[name] = value
    return out


AFTERBURNER_ERROR = [""]  # why the last read failed, for the log


def read_shared(name, size_at=None):
    """The bytes of another program's named shared memory (Windows), or
    (None, why). `size_at(header_bytes)` works out how much to read from its
    first 64 bytes. Opened the way monitoring tools do (OpenFileMapping), which
    also works while that program runs as administrator."""
    import ctypes
    import os
    if os.name != "nt":
        return None, "not Windows"
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenFileMappingW.restype = ctypes.c_void_p
    k32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    k32.MapViewOfFile.restype = ctypes.c_void_p
    k32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
    k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    FILE_MAP_READ = 0x0004
    handle = k32.OpenFileMappingW(FILE_MAP_READ, False, name)
    if not handle:
        err = ctypes.get_last_error()
        return None, ("not running" if err == 2 else f"Windows error {err}")
    try:
        view = k32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, 0)
        if not view:
            return None, f"Windows error {ctypes.get_last_error()}"
        try:
            head = ctypes.string_at(view, 64)
            return ctypes.string_at(view, size_at(head) if size_at else 64), ""
        finally:
            k32.UnmapViewOfFile(view)
    finally:
        k32.CloseHandle(handle)


def read_afterburner():
    """{name: value} from MSI Afterburner if it's running (Windows), else {}."""
    def size(head):
        header_size, count, entry_size = struct.unpack_from("<3I", head, 8)
        return header_size + min(count, 512) * entry_size
    try:
        buf, why = read_shared("MAHMSharedMemory", size)
    except (OSError, struct.error) as e:
        buf, why = None, str(e)
    if buf is None:
        AFTERBURNER_ERROR[0] = ("MSI Afterburner isn't running" if why == "not running" else
                                f"couldn't read MSI Afterburner's readings ({why})")
        return {}
    readings = parse_afterburner(buf)
    AFTERBURNER_ERROR[0] = "" if readings else "MSI Afterburner's readings are empty (is monitoring on?)"
    return readings


# RivaTuner Statistics Server: one entry per app it's drawing on (a game),
# with frames counted over a time window: frames / time = FPS.
_RTSS_SIG = 0x52545353  # "RTSS"


def parse_rtss(buf, now_ms):
    """The frame rate of the app RivaTuner updated most recently (within 2 s
    of now_ms, GetTickCount's clock), or None if no game is running."""
    sig, version, entry_size, arr_offset, arr_size = struct.unpack_from("<5I", buf, 0)
    if sig != _RTSS_SIG or not entry_size:
        return None
    best = None
    for i in range(min(arr_size, 256)):
        at = arr_offset + i * entry_size
        if at + 284 > len(buf):
            break
        pid = struct.unpack_from("<I", buf, at)[0]
        if not pid:
            continue
        t0, t1, frames = struct.unpack_from("<3I", buf, at + 268)
        if t1 <= t0 or (now_ms - t1) & 0xFFFFFFFF > 2000:
            continue  # nothing recent from this app
        fps = frames * 1000.0 / (t1 - t0)
        if not 0 < fps < 2000:
            continue  # not a real frame rate
        if best is None or t1 > best[0]:
            best = (t1, fps)
    return None if best is None else best[1]


def read_rtss_fps():
    import ctypes
    import os
    if os.name != "nt":
        return None
    def size(head):
        _, _, entry_size, arr_offset, arr_size = struct.unpack_from("<5I", head, 0)
        return arr_offset + min(arr_size, 256) * entry_size
    try:
        buf, _ = read_shared("RTSSSharedMemoryV2", size)
    except (OSError, struct.error):
        return None
    if buf is None:
        return None
    return parse_rtss(buf, ctypes.windll.kernel32.GetTickCount())


def pick(readings, *names):
    """The first of `names` Afterburner has, or None. Afterburner marks "no
    data" with the biggest float there is (3.4e38): that counts as none too."""
    for n in names:
        for k, v in readings.items():
            if k.lower() == n.lower():
                return v if v == v and abs(v) < 1e9 else None  # (v == v: not NaN)
    return None


def _lhm_cpu_temp():
    """The CPU temperature from LibreHardwareMonitor's web server, if it's
    running with "Remote web server" on (Windows doesn't give it out otherwise)."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:8085/data.json", timeout=1) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 (not running: no CPU temperature)
        return None
    found = []

    def walk(node, cpu=False):
        text = node.get("Text", "")
        cpu = cpu or "cpu" in text.lower() or "ryzen" in text.lower() or "intel" in text.lower()
        if cpu and any(k in text for k in ("Package", "Tctl", "Tdie", "Core (Tctl/Tdie)")):
            try:
                found.append(float(str(node.get("Value", "")).split()[0].replace(",", ".")))
            except ValueError:
                pass
        for child in node.get("Children", []):
            walk(child, cpu)

    walk(data)
    return found[0] if found else None


def _font(size, bold=False):
    from PIL import ImageFont
    for name in (("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else
                 ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


class Layout:
    """The picture for the screen. Portrait (standing up, 462x1920): the time
    at the top, his face in the middle, the vitals below. Landscape: the face
    in the middle, the time and vitals at the sides."""

    def __init__(self, face, stats, portrait=True, state=lambda: "idle"):
        from PIL import Image
        self.face, self.stats, self.portrait, self.state = face, stats, portrait, state
        self.size = (HEIGHT, WIDTH) if portrait else (WIDTH, HEIGHT)
        self.back = Image.new("RGB", self.size, (0, 0, 0))
        self.f_big, self.f_mid, self.f_small = _font(92, True), _font(46, True), _font(36)

    def _status(self):
        """The bottom line: what he's doing, else the game's frame rate, else his name."""
        st = self.state()
        if st == "listening":
            return "Listening..."
        if st == "thinking":
            return "Thinking..."
        fps = self.stats.fps
        return "Buddy" if fps is None else f"{fps:.0f} FPS"

    def _accent(self):
        p = self.face.cur  # his eye colour: the screen's colours follow his mood
        return (int(p.eyeR), int(p.eyeG), int(p.eyeB))

    def _vitals(self):
        s = self.stats
        f = lambda v, unit, nd=0: "--" if v is None else f"{v:.{nd}f}{unit}"
        if s.cpu_temp is not None:  # the temperature big, the load and clock small
            cpu_big = f(s.cpu_temp, "°C")
            cpu_small = "  ".join(x for x in (f(s.cpu, "% busy"), None if s.mhz is None else f(s.mhz, " MHz")) if x)
        else:
            cpu_big, cpu_small = f(s.cpu, "%"), ("" if s.mhz is None else f(s.mhz, " MHz"))
        return [("CPU", cpu_big, cpu_small), ("GPU", f(s.gpu_temp, "°C"), f(s.gpu_use, "% busy"))]

    def _box(self, d, xy, title, big, small, accent):
        from PIL import ImageDraw  # noqa: F401
        x0, y0, x1, y1 = xy
        d.rounded_rectangle(xy, radius=24, outline=accent, width=4, fill=(14, 14, 22))
        cx = (x0 + x1) // 2
        d.text((cx, y0 + 40), title, font=self.f_small, fill=(200, 200, 210), anchor="mm")
        d.text((cx, (y0 + y1) // 2 + 4), big, font=self.f_big, fill=(255, 255, 255), anchor="mm")
        if small:
            d.text((cx, y1 - 42), small, font=self.f_small, fill=accent, anchor="mm")

    def render(self):
        import datetime as dt
        from PIL import ImageDraw
        img = self.back.copy()
        d = ImageDraw.Draw(img)
        accent = self._accent()
        now = dt.datetime.now()
        clock, date = now.strftime("%I:%M %p").lstrip("0"), now.strftime("%a %b %d")
        (cpu_t, cpu_big, cpu_small), (gpu_t, gpu_big, gpu_small) = self._vitals()
        if self.portrait:
            w = self.size[0]
            d.text((w // 2, 80), clock, font=self.f_mid, fill=(255, 255, 255), anchor="mm")
            d.text((w // 2, 140), date, font=self.f_small, fill=accent, anchor="mm")
            img.paste(self.face.render(w, 640), (0, 200))
            self._box(d, (24, 880, w - 24, 1250), cpu_t, cpu_big, cpu_small, accent)
            self._box(d, (24, 1290, w - 24, 1660), gpu_t, gpu_big, gpu_small, accent)
            d.text((w // 2, 1760), self._status(), font=self.f_mid, fill=accent, anchor="mm")
        else:
            fw = HEIGHT * 4 // 3
            img.paste(self.face.render(fw, HEIGHT), ((WIDTH - fw) // 2, 0))
            d.text((330, 150), clock, font=self.f_big, fill=(255, 255, 255), anchor="mm")
            d.text((330, 250), date, font=self.f_mid, fill=accent, anchor="mm")
            if self.stats.fps is not None:
                d.text((330, 350), f"{self.stats.fps:.0f} FPS", font=self.f_mid, fill=(255, 255, 255), anchor="mm")
            self._box(d, (WIDTH - 600, 30, WIDTH - 330, 432), cpu_t, cpu_big, cpu_small, accent)
            self._box(d, (WIDTH - 300, 30, WIDTH - 30, 432), gpu_t, gpu_big, gpu_small, accent)
        return img


def split_command(text):
    """'"C:\\…\\SignalRgbLauncher.exe" --silent' -> (the path, "--silent")."""
    import re
    t = text.strip()
    if t.startswith('"') and '"' in t[1:]:
        end = t.index('"', 1)
        return t[1:end], t[end + 1:].strip()
    m = re.match(r"^(.*?\.(?:exe|lnk|bat|cmd))(?:\s+(.*))?$", t, re.I)
    if m:
        return m.group(1), (m.group(2) or "").strip()
    return t, ""


def find_shortcut(path):
    """A Start menu shortcut for `path` when that exact file isn't there:
    "…\\SignalRgbLauncher.exe" can be "SignalRgbLauncher.exe.lnk", or a
    shortcut with a similar name in the same folder ("SignalRGB.lnk")."""
    import os
    import re
    for alt in (path + ".lnk", os.path.splitext(path)[0] + ".lnk"):
        if os.path.exists(alt):
            return alt
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        return None
    key = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    want = key(os.path.splitext(os.path.basename(path))[0]).replace("launcher", "")
    for f in sorted(os.listdir(folder)):
        stem = key(os.path.splitext(f)[0])
        if "uninstall" in stem or "remove" in stem or "setup" in stem:
            continue  # never start an uninstaller or installer
        if f.lower().endswith((".lnk", ".exe")) and stem and (stem in want or want in stem):
            return os.path.join(folder, f)
    return None


def start_after(path, log):
    """Starts a program once he has the screen (SignalRGB: it opens the screen
    at startup even while it's disabled there, and then he can't), unless
    it's already running."""
    import os
    import subprocess
    if not path:
        return
    path, args = split_command(path)
    exe = os.path.basename(path).lower()
    try:
        import psutil
        names = {exe}
        if "signalrgb" in exe:  # its launcher starts SignalRgb.exe
            names.add("signalrgb.exe")
        if any((p.info.get("name") or "").lower() in names for p in psutil.process_iter(["name"])):
            return  # already running
    except Exception:  # noqa: BLE001
        pass
    # A Start menu entry is a shortcut: "…\\SignalRgbLauncher.exe" there is really
    # "SignalRgbLauncher.exe.lnk" (or ".lnk" in place of ".exe").
    if not os.path.exists(path):
        path = find_shortcut(path) or path
    if not args and "signalrgb" in os.path.basename(path).lower():
        args = "--silent"  # to the tray, no window (as its own autostart does)
    try:
        if path.lower().endswith(".lnk") and hasattr(os, "startfile"):
            os.startfile(path, "open", args)  # opens a shortcut like double-clicking it
        else:
            subprocess.Popen([path] + args.split(), cwd=os.path.dirname(path) or None, close_fds=True)
        log(f"Trofeo screen: started {os.path.basename(path)} now that the screen is his")
    except OSError as e:
        there = ""
        folder = os.path.dirname(path)
        if os.path.isdir(folder):
            there = f"; that folder has: {', '.join(sorted(os.listdir(folder))[:12])}"
        log(f"Trofeo screen: couldn't start {path} ({e}){there}; check start_after_screen in buddy.ini")


def program_names(path):
    """The process names a start_after_screen program runs as."""
    import ntpath  # a Windows path, split the Windows way (also when tested elsewhere)
    import os
    exe, _ = split_command(path)
    names = {ntpath.basename(os.path.splitext(exe)[0] if exe.lower().endswith(".lnk") else exe).lower()}
    names = {n if n.endswith(".exe") else n + ".exe" for n in names}
    if any("signalrgb" in n for n in names):
        names |= {"signalrgb.exe", "signalrgblauncher.exe"}
    return names


def stop_program(path, log):
    """Closes the start_after_screen program (it took the screen). True if it was running."""
    try:
        import psutil
    except ImportError:
        return False
    names = program_names(path)
    procs = [p for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() in names]
    if not procs:
        return False
    log(f"Trofeo screen: {procs[0].info['name']} took the screen (after the PC woke?): closing it, then "
        "starting it again once the screen is his")
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=5)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    return True


def run(face, cfg, log, stop=None, state=lambda: "idle"):
    """Keeps the screen showing the face (a thread). Reconnects if the screen
    goes away (unplugged, or TRCC took it)."""
    stop = stop or threading.Event()
    fps = max(2, min(30, cfg.get("trofeo_fps", 20)))
    portrait = cfg.get("trofeo_orientation", "portrait") != "landscape"
    layout = Layout(face, Stats(log), portrait, state)
    screen, warned = None, False
    shown, t_report = 0, time.time()
    last_reclaim = 0.0
    after = cfg.get("start_after_screen", "")
    while not stop.is_set():
        if screen is None:
            try:
                screen = TrofeoScreen(log, cfg.get("trofeo_rotate", "auto"), portrait)
                warned = False
                start_after(after, log)
            except Exception as e:  # noqa: BLE001 (not there, or busy: try again later)
                if not warned:
                    log(f"Trofeo screen: {e}")
                    warned = True
                # After the PC wakes from sleep, the screen comes back as if newly
                # plugged in, and the program that should start after him (SignalRGB)
                # may grab it first. Close it, take the screen, start it again.
                busy = "access denied" in str(e).lower() or "busy" in str(e).lower()
                if busy and after and time.time() - last_reclaim > 60:
                    last_reclaim = time.time()
                    if stop_program(after, log):
                        stop.wait(2)
                        continue  # try for the screen again now
                stop.wait(5)
                continue
        t0 = time.time()
        try:
            screen.show(layout.render())
            shown += 1
        except Exception as e:  # noqa: BLE001
            log(f"Trofeo screen stopped answering ({e}); trying again")
            screen = None
            continue
        if time.time() - t_report > 60:
            log(f"(Trofeo screen: {shown / (time.time() - t_report):.1f} pictures a second)", debug=True)
            shown, t_report = 0, time.time()
        stop.wait(max(0.0, 1.0 / fps - (time.time() - t0)))
