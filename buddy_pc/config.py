"""buddy.ini: the settings, with defaults for anything left out."""

import configparser
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(HERE, "buddy.ini")


def _on(v):
    return str(v).strip().lower() in ("1", "on", "yes", "true")


def _url(v):
    """'192.168.4.2' -> 'http://192.168.4.2' (an address without http:// is fine)."""
    v = v.strip().rstrip("/")
    return v if not v or "://" in v else "http://" + v


def load(path=CONFIG_FILE):
    # Windows hides extensions: "buddy.ini" typed in Explorer can be buddy.ini.ini or .txt.
    found = next((p for p in (path, path + ".ini", path + ".txt") if os.path.isfile(p)), None)
    if not found:
        sys.exit(f"Missing {path}: copy buddy.example.ini to buddy.ini and fill it in.")
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(found, encoding="utf-8-sig")
    if "buddy" not in cp:
        sys.exit(f"{found} has no [buddy] line at the top: copy buddy.example.ini again.")
    c = cp["buddy"]
    g = lambda k, d="": c.get(k, d).strip()
    return {
        "owner": g("owner_name", "my owner"),
        # Brain
        "ollama": _url(g("ollama_url", "http://localhost:11434")),
        "chat_model": g("chat_model", "qwen3:4b-instruct"),
        # The same as Home Assistant's Ollama "context window size", so the
        # desk Buddy and the PC share one loaded copy of the model.
        "context_size": int(g("context_size", "8192")),
        "personality_file": os.path.join(HERE, g("personality_file", "personality.txt")),
        # Ears
        "whisper_model": g("whisper_model", "large-v3-turbo"),
        "whisper_device": g("whisper_device", "auto").lower(),  # auto, cuda or cpu
        "language": g("language", "en"),
        "wake_word": g("wake_word", "hey_jarvis"),  # an openWakeWord model name or .onnx file; off = click only
        "wake_threshold": float(g("wake_threshold", "0.5")),
        "follow_up": _on(g("follow_up", "on")),
        "mic": g("microphone", ""),        # part of the device name, or empty for Windows' default
        "speaker": g("speaker", ""),
        # Voice
        "kokoro_host": g("kokoro_host", "localhost"),
        "kokoro_port": int(g("kokoro_port", "10210")),
        "voice": g("voice", "af_heart"),
        "volume": max(0, min(200, int(g("volume", "100")))),
        # Home Assistant's MQTT broker: memory notes shared with the desk Buddy
        "mqtt_host": g("mqtt_host", ""),
        "mqtt_port": int(g("mqtt_port", "1883")),
        "mqtt_user": g("mqtt_user", ""),
        "mqtt_password": g("mqtt_password", ""),
        # Tools: web search (SearXNG), weather (Open-Meteo), looking (webcam + vision model)
        "search_url": _url(g("search_url", "")),
        "weather_place": g("weather_place", ""),
        "units": g("units", "us").lower(),
        "vision_model": g("vision_model", ""),
        "camera_index": int(g("camera_index", "0")),
        # Voice ID (tools/voiceid in the desk Buddy's project): who's talking
        "voice_id_url": _url(g("voice_id_url", "")),
        # Buddy's eyes (shared diary)
        "eyes_diary": g("eyes_diary", ""),
        # Window
        "window_width": max(160, int(g("window_width", "320"))),
        "always_on_top": _on(g("always_on_top", "on")),
    }
