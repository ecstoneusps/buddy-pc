"""What's on the PC: the window in front (its app and title) and, when
asked, a picture of the screen for the vision model. Read only, and
nothing leaves the PC (the screenshot goes to Ollama, here)."""

import base64
import io
import os


def active_window():
    """('Microsoft Edge', 'Good Mythical Morning - YouTube'), or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = ""
        try:
            import psutil
            app = psutil.Process(pid.value).name()
        except Exception:  # noqa: BLE001
            pass
        return app_label(app, buf.value), clean_title(buf.value)
    except Exception:  # noqa: BLE001
        return None


_APPS = {"msedge.exe": "Microsoft Edge", "chrome.exe": "Chrome", "firefox.exe": "Firefox", "steam.exe": "Steam",
         "discord.exe": "Discord", "spotify.exe": "Spotify", "code.exe": "VS Code", "explorer.exe": "File Explorer",
         "winword.exe": "Word", "excel.exe": "Excel", "powershell.exe": "PowerShell", "windowsterminal.exe": "Terminal",
         "notepad.exe": "Notepad", "obs64.exe": "OBS", "signalrgb.exe": "SignalRGB"}


def app_label(exe, title=""):
    e = (exe or "").lower()
    return _APPS.get(e, exe.removesuffix(".exe").removesuffix(".EXE") if exe else "an app")


def clean_title(title):
    """Drops the browser's own name from the end of a window title."""
    for tail in (" - Personal - Microsoft​ Edge", " - Microsoft​ Edge", " - Microsoft Edge",
                 " - Google Chrome", " — Mozilla Firefox", " - Mozilla Firefox"):
        if title.endswith(tail):
            return title[: -len(tail)]
    return title


def screenshot_jpeg(width=1280):
    """The screen(s) as a JPEG, scaled down, or None."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True)
    except Exception:  # noqa: BLE001 (not Windows, or no desktop)
        return None
    if img.width > width:
        img = img.resize((width, int(img.height * width / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def look_at_screen(cfg, question, log):
    """Asks the vision model about what's on the screen right now."""
    import requests
    jpg = screenshot_jpeg()
    if jpg is None:
        return "You couldn't get a picture of the screen just now."
    prompt = (f"This is a screenshot of {cfg['owner']}'s PC screen. {question} Answer briefly, only from what "
              "you can clearly see; read any text that matters (an error message, a title). If you can't tell, "
              "say so.")
    body = {"model": cfg["vision_model"], "stream": False, "keep_alive": "10m",
            "options": {"temperature": 0.2, "repeat_penalty": 1.15, "num_predict": 200},
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(jpg).decode()]}]}
    r = requests.post(f"{cfg['ollama']}/api/chat", json=body, timeout=120)
    r.raise_for_status()
    seen = r.json().get("message", {}).get("content", "").strip()
    log(f"(looked at the screen: {seen})")
    return f"On the screen you see: {seen}"
