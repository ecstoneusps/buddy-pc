"""Things he can do while answering (Ollama tool calls): search the web
(SearXNG), check the weather (Open-Meteo, free, no key) and look through the
webcam (the vision model Buddy's eyes uses)."""

import base64
import datetime as dt
import json
import os

import requests

# ---------------------------------------------------------------------------
# What the AI is told it can do


def definitions(cfg):
    tools = []
    if cfg.get("search_url"):
        tools.append({"type": "function", "function": {
            "name": "web_search",
            "description": "Search the web for current or factual information you don't know for sure: news, "
                           "sports results, prices, opening hours, facts about people, places and things.",
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "What to search for, like a search engine query"}},
                "required": ["query"]}}})
    if cfg.get("weather_place"):
        tools.append({"type": "function", "function": {
            "name": "get_weather",
            "description": "The weather where your owner lives: now, and the forecast for today and the next two "
                           "days. Use it for any question about weather, temperature, rain, snow or wind.",
            "parameters": {"type": "object", "properties": {}}}})
    if cfg.get("ha_url") and cfg.get("ha_token"):
        tools.append({"type": "function", "function": {
            "name": "control_home",
            "description": "Control or check your owner's home devices through Home Assistant: lights, lamps, "
                           "switches, fans, the thermostat, locks, the garage door, blinds. Turn things on or off, "
                           "dim, set a temperature, or ask whether something is on, open or locked.",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "description": "The command in plain words, naming the device and "
                                                             "room, like 'turn off the kitchen lights'"}},
                "required": ["command"]}}})
    if cfg.get("vision_model"):
        tools.append({"type": "function", "function": {
            "name": "look",
            "description": "Look through your camera at your owner and the desk right now, to answer questions "
                           "about what you can see: what they're holding, wearing or showing you, how many fingers "
                           "they hold up, how they look.",
            "parameters": {"type": "object", "properties": {
                "question": {"type": "string", "description": "What you want to find out from the picture"}},
                "required": ["question"]}}})
    return tools


def run(cfg, name, args, log):
    """Carries out a tool call; returns text for the AI."""
    try:
        if name == "web_search":
            return web_search(cfg, str(args.get("query", "")))
        if name == "get_weather":
            return weather(cfg)
        if name == "look":
            return look(cfg, str(args.get("question", "What do you see?")), log)
        if name == "control_home":
            from . import home
            done, said = home.command(cfg, str(args.get("command", "")), log)
            return (f"Home Assistant {'did it' if done else 'could not do it'} and said: {said or '(nothing)'}. "
                    "Tell your owner briefly, in your own words.")
    except Exception as e:  # noqa: BLE001 (tell the AI it didn't work, rather than crash)
        log(f"{name} didn't work: {e}")
        return f"That didn't work ({type(e).__name__}). Tell your owner you couldn't check just now."
    return f"There's no tool called {name}."


# ---------------------------------------------------------------------------
# Web search


def web_search(cfg, query, results=5):
    r = requests.get(f"{cfg['search_url']}/search", params={"q": query, "format": "json", "language": "en"},
                     timeout=10)
    r.raise_for_status()
    d = r.json()
    out = []
    for a in d.get("answers", [])[:2]:
        out.append(f"Answer: {a if isinstance(a, str) else a.get('answer', '')}")
    for x in d.get("results", [])[:results]:
        out.append(f"- {x.get('title', '')}: {x.get('content', '')} ({x.get('url', '')})")
    return ("Search results for \"" + query + "\":\n" + "\n".join(out)) if out else f"No results for \"{query}\"."


# ---------------------------------------------------------------------------
# Weather (Open-Meteo)

_WEATHER_CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
                  51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle",
                  57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
                  67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
                  80: "rain showers", 81: "rain showers", 82: "heavy rain showers", 85: "snow showers",
                  86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
                  99: "thunderstorms with hail"}
_place_cache = {}


def _place(name):
    if name not in _place_cache:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name.split(",")[0].strip(), "count": 10, "language": "en"}, timeout=10)
        r.raise_for_status()
        found = r.json().get("results") or []
        if not found:
            raise ValueError(f"couldn't find the place \"{name}\" (weather_place in buddy.ini)")
        # "Springfield, Ohio": prefer the match whose region or country fits the rest.
        hint = name.split(",", 1)[1].strip().lower() if "," in name else ""
        best = next((p for p in found if hint and hint in (p.get("admin1", "") + " " + p.get("country", "")).lower()),
                    found[0])
        _place_cache[name] = (best["latitude"], best["longitude"], best.get("name", name))
    return _place_cache[name]


def weather(cfg):
    lat, lon, town = _place(cfg["weather_place"])
    metric = cfg.get("units", "us") == "metric"
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "temperature_unit": "celsius" if metric else "fahrenheit",
        "wind_speed_unit": "kmh" if metric else "mph"}, timeout=10)
    r.raise_for_status()
    d = r.json()
    deg, wind = ("°C", "km/h") if metric else ("°F", "mph")
    c = d["current"]
    lines = [f"Weather in {town} now: {_WEATHER_CODES.get(c['weather_code'], 'unknown')}, "
             f"{c['temperature_2m']:.0f}{deg} (feels like {c['apparent_temperature']:.0f}{deg}), "
             f"wind {c['wind_speed_10m']:.0f} {wind}."]
    day = d["daily"]
    names = ["Today", "Tomorrow"]
    for i, date in enumerate(day["time"][:3]):
        name = names[i] if i < 2 else dt.date.fromisoformat(date).strftime("%A")
        lines.append(f"{name}: {_WEATHER_CODES.get(day['weather_code'][i], 'unknown')}, high "
                     f"{day['temperature_2m_max'][i]:.0f}{deg}, low {day['temperature_2m_min'][i]:.0f}{deg}, "
                     f"{day['precipitation_probability_max'][i] or 0}% chance of rain or snow.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Looking (the webcam and the vision model)


def snapshot(index):
    """One JPEG from the webcam, opened just for this (Buddy's eyes uses it too)."""
    import cv2
    cam = cv2.VideoCapture(index, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(index)
    try:
        if not cam.isOpened():
            return None
        frame = None
        for _ in range(8):  # let the exposure settle
            ok, f = cam.read()
            if ok:
                frame = f
        if frame is None:
            return None
        h, w = frame.shape[:2]
        if w > 768:
            frame = cv2.resize(frame, (768, int(h * 768 / w)))
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return jpg.tobytes() if ok else None
    finally:
        cam.release()


def look(cfg, question, log, jpg=None):
    """What the vision model sees: through the webcam, or in `jpg` (a photo
    from the phone app)."""
    import time
    phone = jpg is not None
    if not phone:
        jpg = snapshot(cfg["camera_index"])
        if jpg is None:  # Buddy's eyes may have it open this second
            time.sleep(1.5)
            jpg = snapshot(cfg["camera_index"])
    if jpg is None:
        return "You couldn't get a picture from your camera just now."
    what = (f"This is a photo from {cfg['owner']}'s phone camera (they may be away from home; it may show "
            f"{cfg['owner']} or what they're pointing it at)." if phone
            else f"This is a webcam picture of {cfg['owner']} at their desk.")
    prompt = (f"{what} {question} Answer briefly and only "
              "from what you can clearly see; if you can't tell, say so. To count fingers, count each raised "
              "finger one by one.")
    body = {"model": cfg["vision_model"], "stream": False, "keep_alive": "10m",
            # A short, steady answer: no rambling or getting stuck repeating itself.
            "options": {"temperature": 0.2, "repeat_penalty": 1.15, "num_predict": 150},
            "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(jpg).decode()]}]}
    for attempt in (1, 2):
        r = requests.post(f"{cfg['ollama']}/api/chat", json=body, timeout=120)
        if r.ok:
            break
        # Ollama says what went wrong in the body ({"error": "..."}).
        log(f"the vision model failed (HTTP {r.status_code}): {r.text[:300]}")
        if attempt == 1:
            time.sleep(1.0)
    r.raise_for_status()
    seen = r.json().get("message", {}).get("content", "").strip()
    log(f"(looked{' through the phone' if phone else ''}: {seen})")
    return f"Through {'the phone' if phone else 'your'} camera you see: {seen}"
