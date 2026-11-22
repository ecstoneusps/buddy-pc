"""Tests that run without a microphone, speakers, GPU, Ollama or Kokoro:
python -m pytest tests  (or python tests/test_buddy.py)"""

import json
import os
import queue
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from buddy_pc import textutil as T  # noqa: E402
from buddy_pc.face import EXPRESSIONS, Face, expression_for  # noqa: E402
from buddy_pc.hearing import Endpointer  # noqa: E402
from buddy_pc import voice  # noqa: E402


# ---------------------------------------------------------------------------
# Text


def test_split_reply():
    assert T.split_reply("[laughing] Ha! **Got** you 🚀") == ("laughing", "Ha! Got you")
    assert T.split_reply("No tag here.") == ("", "No tag here.")
    assert T.split_reply("<think>hmm</think>[Happy] Hi") == ("happy", "Hi")


def test_sentence_end():
    s = "[happy] Sure thing, Eric! The moon is far away. Very far."
    a = T.sentence_end(s, 0)
    assert s[:a].strip() == "[happy] Sure thing, Eric!"
    b = T.sentence_end(s, a)
    assert s[a:b].strip() == "The moon is far away."
    assert T.sentence_end(s, b) == -1  # "Very far." is the end of the text: not complete yet
    t = "Hi. There is more. And"
    assert t[:T.sentence_end(t, 0)] == "Hi. There is more. "  # "Hi." alone waits for more


def test_cancel_and_clarify():
    for yes in ("Cancel.", "Cancer.", "Never mind", "Hey Buddy, cancel", "I wasn't talking to you.",
                "No, that was YouTube."):
        assert T.is_cancel(yes), yes
    assert T.is_cancel("Cancel. What do you want me to say?")
    assert T.is_cancel("Never mind, it's fine, go for the jug")
    for no in ("Tell me about cancer.", "forget it", "Who was the first person on the moon?"):
        assert not T.is_cancel(no), no
    assert T.is_clarify("I'm not sure I caught that part. Could you say it again?")
    assert not T.is_clarify("Saturn has 146 moons. Want to hear about the biggest one?")


def test_notes_third_person():
    assert T.to_third_person("My dog is King") == "Your owner's dog is King"
    assert T.to_third_person("I like pizza and my cat likes fish") == \
        "Your owner likes pizza and their cat likes fish"
    block = T.notes_block(T.parse_notes("# comment\nMy dog is King\n\nI drink chocolate milk\n"))
    assert "Your owner's dog is King; Your owner drinks chocolate milk" in block


# ---------------------------------------------------------------------------
# Face


def test_face_renders_every_expression():
    f = Face(seed=1)
    for e in EXPRESSIONS:
        f.set_expression(e)
        f.set_talk_level(0.7 if e == "happy" else 0)
        for _ in range(20):
            f.update(33)
        img = f.render(320, 240)
        assert img.size == (320, 240)
        px = np.asarray(img)
        assert px.max() > 100, e   # something is drawn
        assert px[0, 0].max() < 10, e  # on a black background


def test_expression_for():
    assert expression_for("loving") == "love"
    assert expression_for("Laughing") == "laughing"
    assert expression_for("pizza") is None


# ---------------------------------------------------------------------------
# Hearing


class FakeMic:
    def __init__(self, frames):
        self.frames = list(frames)

    def read(self, timeout=1.0):
        if not self.frames:
            import queue
            raise queue.Empty
        time.sleep(0.08)  # real time: 80 ms frames
        return self.frames.pop(0)


def _tone(db, n=1280):
    amp = 32768 * 10 ** (db / 20) * np.sqrt(2)
    return (amp * np.sin(np.arange(n) * 2 * np.pi * 300 / 16000)).astype(np.int16)


def test_find_device_prefers_mme_and_inputs():
    from buddy_pc.hearing import find_device
    apis = [{"name": "MME"}, {"name": "Windows WASAPI"}]
    devs = [{"name": "Headset Microphone (Oculus Virtual Audio)", "hostapi": 0, "max_input_channels": 1,
             "max_output_channels": 0},
            {"name": "Microphone (HD Pro Webcam C920)", "hostapi": 1, "max_input_channels": 2,
             "max_output_channels": 0},
            {"name": "Microphone (HD Pro Webcam C92", "hostapi": 0, "max_input_channels": 2,
             "max_output_channels": 0},
            {"name": "Speakers (Webcam)", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2}]
    assert find_device("webcam", "input", devs, apis) == 2      # the MME one
    assert find_device("", "input", devs, apis) is None         # Windows' default
    assert find_device("webcam", "output", devs, apis) == 3
    try:
        find_device("yeti", "input", devs, apis)
        assert False
    except ValueError:
        pass


def test_endpointer_records_speech_then_stops():
    quiet = [_tone(-65) for _ in range(5)]
    speech = [_tone(-25) for _ in range(10)]
    after = [_tone(-65) for _ in range(15)]
    ep = Endpointer(start_window=3, end_silence=0.5)
    pcm = ep.record(FakeMic(quiet + speech + after))
    assert pcm is not None
    assert 10 * 1280 <= len(pcm) <= 22 * 1280  # the speech, a little before, the pause after


def test_whisper_hallucinations_dropped():
    from types import SimpleNamespace as S
    from buddy_pc.hearing import hallucination
    assert hallucination(S(text=" Thank you.", no_speech_prob=0.4, avg_logprob=-0.3))
    assert hallucination(S(text=" Anything at all", no_speech_prob=0.8, avg_logprob=-0.2))
    assert not hallucination(S(text=" Thank you.", no_speech_prob=0.02, avg_logprob=-0.2))  # you, clearly
    assert not hallucination(S(text=" What day is it?", no_speech_prob=0.1, avg_logprob=-0.3))


def test_endpointer_ignores_the_echo():
    loud = [_tone(-25) for _ in range(6)]   # his echo, 0.5 s
    quiet = [_tone(-65) for _ in range(15)]
    ep = Endpointer(start_window=1.0, end_silence=0.5)
    assert ep.record(FakeMic(loud + quiet), ignore_first=0.6) is None


def test_endpointer_ignores_loud_noise_with_a_voice_detector():
    class NoVoice:
        def reset_states(self):
            pass

        def predict(self, frame, frame_size=640):
            return 0.05  # a fan: loud, but not a voice

    ep = Endpointer(start_window=0.6, vad=NoVoice())
    assert ep.record(FakeMic([_tone(-25) for _ in range(12)])) is None


def test_endpointer_follow_up_needs_your_loudness():
    quiet_voice = [_tone(-40) for _ in range(10)] + [_tone(-65) for _ in range(10)]
    ep = Endpointer(start_window=1.2, end_silence=0.5)
    assert ep.record(FakeMic(list(quiet_voice)), min_db=-24 - 10) is None   # a fan or the TV
    ep = Endpointer(start_window=1.2, end_silence=0.5)
    assert ep.record(FakeMic(list(quiet_voice))) is not None                 # without the limit: heard


def test_endpointer_nothing_said():
    ep = Endpointer(start_window=0.5)
    assert ep.record(FakeMic([_tone(-65) for _ in range(20)])) is None


# ---------------------------------------------------------------------------
# Voice: a fake Kokoro speaking the Wyoming protocol


def _fake_kokoro(rate=24000):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    got = {}

    def serve():
        conn, _ = srv.accept()
        f = conn.makefile("rb")
        etype, data, _ = voice.read_event(f)
        got["type"], got["data"] = etype, data
        pcm = (np.arange(rate // 2) % 100).astype(np.int16).tobytes()
        fmt = {"rate": rate, "width": 2, "channels": 1}

        def send(t, d, payload=b""):
            body = json.dumps(d).encode()
            conn.sendall(json.dumps({"type": t, "data_length": len(body), "payload_length": len(payload)}).encode()
                         + b"\n" + body + payload)

        send("audio-start", fmt)
        send("audio-chunk", fmt, pcm[: len(pcm) // 2])
        send("audio-chunk", fmt, pcm[len(pcm) // 2:])
        send("audio-stop", {})
        conn.close()
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1], got


def test_kokoro_wyoming_client():
    port, got = _fake_kokoro()
    rate, pcm = voice.synthesize("127.0.0.1", port, "Hello there!", "af_heart")
    assert got["type"] == "synthesize"
    assert got["data"] == {"text": "Hello there!", "voice": {"name": "af_heart"}}
    assert rate == 24000 and len(pcm) == 12000


def test_boost_is_louder_without_clipping():
    t = np.arange(24000) / 24000
    quiet = (np.sin(2 * np.pi * 220 * t) * (0.2 + 0.3 * (t % 0.5 > 0.25)) * 30000).astype(np.int16)
    y = voice.boost(quiet, 2.0)
    rms = lambda a: float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
    assert np.abs(y).max() <= 30000                       # never clips
    assert 20 * np.log10(rms(y) / rms(quiet)) > 4.5       # but clearly louder
    assert np.array_equal(voice.boost(quiet, 1.0), quiet)


def test_quiet_speech_is_brought_up():
    t = np.arange(24000) / 24000
    quiet = (np.sin(2 * np.pi * 220 * t) * 0.05 * 32767 * (t % 0.4 > 0.1)).astype(np.int16)  # about -29 dB
    gain, before = voice.level_gain(quiet)
    assert -31 < before < -27 and 3 < gain < 6
    loud = voice.boost(quiet, gain)
    assert abs(voice.rms_db(loud) - voice.TARGET_DB) < 1.5 and np.abs(loud).max() <= 30000


def test_speaker_plays_and_ducks_other_apps():
    import sys as _sys
    import types as _types
    from unittest import mock
    played, events, logs = [], [], []

    class Out:
        def __init__(self, **k):
            pass

        def start(self):
            pass

        def write(self, chunk):
            played.append(len(chunk))

        def stop(self):
            pass

        def close(self):
            pass

        def abort(self):
            pass

    sd = _types.ModuleType("sounddevice")
    sd.OutputStream = Out

    class FakeDucker:
        def __init__(self, *a, **k):
            pass

        def duck(self):
            events.append("duck")

        def restore(self):
            events.append("restore")

    t = np.arange(24000) / 24000
    speech = (np.sin(2 * np.pi * 200 * t) * 3000).astype(np.int16)
    with mock.patch.dict(_sys.modules, {"sounddevice": sd}), \
            mock.patch.object(voice, "synthesize", lambda *a, **k: (24000, speech)), \
            mock.patch.object(voice, "Ducker", FakeDucker):
        sp = voice.Speaker({"kokoro_host": "x", "kokoro_port": 1, "voice": "v", "volume": 100, "speaker": "",
                            "duck_others": 40}, lambda m, **k: logs.append(m))
        sp.say("Hello there, Eric!")
        sp.end()
        assert sp.wait(5)
        time.sleep(0.3)
    assert sum(played) >= 24000 * 0.9                      # it played
    assert not any("couldn't play" in m for m in logs), logs
    assert events[:1] == ["duck"] and events[-1] == "restore"  # turned the others down, then back up


def test_level_of():
    assert voice.level_of(np.zeros(320, dtype=np.int16)) == 0.0
    assert voice.level_of(_tone(-12, 320)) > 0.9


# ---------------------------------------------------------------------------
# Brain: a fake Ollama and a fake desk Buddy


def test_brain_streams_and_uses_notes():
    import tempfile
    from buddy_pc.brain import Brain

    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):  # Ollama's /api/chat
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            for piece in ("[happy] Hi ", "Eric!"):
                self.wfile.write(json.dumps({"message": {"content": piece}, "done": False}).encode() + b"\n")
            self.wfile.write(json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    d = tempfile.mkdtemp()
    cfg = {"owner": "Eric", "ollama": url, "chat_model": "m", "personality_file": os.path.join(d, "none.txt"),
           "eyes_diary": "", "notes_cache": os.path.join(d, "memories.json")}
    b = Brain(cfg, lambda *a, **k: None)
    b.shared.apply(json.dumps({"version": 3, "from": "desk", "notes": "# notes\nMy dog is King\n"}))
    reply = "".join(b.chat("hello"))
    srv.shutdown()
    assert reply == "[happy] Hi Eric!"
    system = seen["body"]["messages"][0]["content"]
    assert "Your owner's dog is King" in system and "Eric's companion" in system
    assert seen["body"]["messages"][-1] == {"role": "user", "content": "hello"}
    assert [h[1] for h in b.history] == ["user", "assistant"]


def test_brain_uses_web_search_then_answers():
    import tempfile
    from buddy_pc.brain import Brain

    seen = {"chats": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # SearXNG
            seen["search"] = self.path
            body = json.dumps({"results": [{"title": "Hot dog contest", "content": "Joey Chestnut won with 70.",
                                            "url": "https://example.com"}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # Ollama
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["chats"].append(body)
            self.send_response(200)
            self.end_headers()
            if len(seen["chats"]) == 1:
                call = {"function": {"name": "web_search", "arguments": {"query": "hot dog contest winner"}}}
                lines = [{"message": {"content": "[thinking] Let me look.", "tool_calls": [call]}, "done": True}]
            else:
                lines = [{"message": {"content": "[happy] Joey Chestnut, with 70!"}, "done": True}]
            for l in lines:
                self.wfile.write(json.dumps(l).encode() + b"\n")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    d = tempfile.mkdtemp()
    cfg = {"owner": "Eric", "ollama": url, "chat_model": "m", "personality_file": os.path.join(d, "x"),
           "eyes_diary": "", "notes_cache": os.path.join(d, "m.json"), "search_url": url}
    b = Brain(cfg, lambda *a, **k: None)
    reply = "".join(b.chat("who won the hot dog contest?"))
    srv.shutdown()
    assert reply == "[thinking] Let me look. [happy] Joey Chestnut, with 70!"
    assert "q=hot+dog+contest+winner" in seen["search"]
    first, second = seen["chats"]
    assert [t["function"]["name"] for t in first["tools"]] == ["web_search"]
    assert "You can search the web" in first["messages"][0]["content"]
    assert second["messages"][-1]["role"] == "tool" and "Joey Chestnut won" in second["messages"][-1]["content"]


def test_brain_nudges_when_he_only_promises_to_check():
    import tempfile
    from buddy_pc.brain import Brain
    chats = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            chats.append(body)
            self.send_response(200)
            self.end_headers()
            n = len(chats)
            if n == 1:
                m = {"content": "[happy] Let me check the weather for you."}
            elif n == 2:
                m = {"content": "", "tool_calls": [{"function": {"name": "get_weather", "arguments": {}}}]}
            else:
                m = {"content": "[cool] Sunny and 70!"}
            self.wfile.write(json.dumps({"message": m, "done": True}).encode() + b"\n")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    d = tempfile.mkdtemp()
    cfg = {"owner": "Eric", "ollama": url, "chat_model": "m", "personality_file": os.path.join(d, "x"),
           "eyes_diary": "", "notes_cache": os.path.join(d, "m.json"), "weather_place": "Nowhere"}
    b = Brain(cfg, lambda *a, **k: None)
    from unittest import mock
    from buddy_pc import tools
    with mock.patch.object(tools, "weather", lambda cfg: "Sunny, 70F."):
        reply = "".join(b.chat("what's the weather?"))
    srv.shutdown()
    assert reply == "[happy] Let me check the weather for you. [cool] Sunny and 70!"
    assert chats[1]["messages"][-1]["content"].startswith("[Go ahead")
    assert chats[2]["messages"][-1] == {"role": "tool", "content": "Sunny, 70F.", "tool_name": "get_weather"}


def test_weather_text():
    from unittest import mock
    from buddy_pc import tools

    class R:
        def __init__(self, d):
            self.d = d

        def raise_for_status(self):
            pass

        def json(self):
            return self.d

    geo = {"results": [{"latitude": 1, "longitude": 2, "name": "Springfield", "admin1": "Illinois", "country": "US"},
                       {"latitude": 3, "longitude": 4, "name": "Springfield", "admin1": "Ohio", "country": "US"}]}
    fc = {"current": {"temperature_2m": 41.2, "apparent_temperature": 35.0, "weather_code": 3,
                      "wind_speed_10m": 9.6, "precipitation": 0},
          "daily": {"time": ["2026-11-19", "2026-11-20", "2026-11-21"], "temperature_2m_max": [45, 50, 38],
                    "temperature_2m_min": [30, 33, 25], "precipitation_probability_max": [10, 60, None],
                    "weather_code": [3, 61, 71]}}
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(params)
        return R(geo if "geocoding" in url else fc)

    with mock.patch.object(tools.requests, "get", get):
        text = tools.weather({"weather_place": "Springfield, Ohio"})
    assert calls[1]["latitude"] == 3  # the Ohio one
    assert text.startswith("Weather in Springfield now: overcast, 41°F (feels like 35°F), wind 10 mph.")
    assert "Tomorrow: light rain, high 50°F, low 33°F, 60% chance" in text and "Saturday: light snow" in text


def test_voice_id():
    from buddy_pc import voiceid
    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got["len"] = int(self.headers["Content-Length"])
            body = json.dumps({"decision": "owner", "score": 0.52, "enrolled": True, "seconds": 2.0}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {"voice_id_url": f"http://127.0.0.1:{srv.server_address[1]}"}
    quiet = lambda *a, **k: None
    assert voiceid.identify(cfg, np.zeros(32000, np.int16), quiet) == "owner" and got["len"] == 64000
    assert voiceid.identify(cfg, np.zeros(8000, np.int16), quiet) is None  # too short to tell
    srv.shutdown()
    assert voiceid.identify({"voice_id_url": ""}, np.zeros(32000, np.int16), quiet) is None


def test_shared_notes_newest_wins_and_survive_a_restart():
    import tempfile
    from buddy_pc.shared import SharedNotes

    path = os.path.join(tempfile.mkdtemp(), "memories.json")
    n = SharedNotes({}, lambda *a, **k: None, cache_file=path)
    assert n.notes() == []
    assert n.apply(json.dumps({"version": 2, "from": "desk", "notes": "My dog is King\n"}), "buddy/desk_buddy_x/memories")
    assert not n.apply(json.dumps({"version": 1, "from": "desk", "notes": "old\n"}))  # older: ignored
    assert not n.apply("not json")
    assert n.notes() == ["My dog is King"] and n.topic == "buddy/desk_buddy_x/memories"
    again = SharedNotes({}, lambda *a, **k: None, cache_file=path)  # the desk Buddy is off: the copy here
    assert again.notes() == ["My dog is King"] and again.version == 2
    sent = []
    again.client = type("C", (), {"publish": lambda self, *a, **k: sent.append((a, k))})()
    again.topic = "buddy/desk_buddy_x/memories"
    assert again.share("My dog is King\nI drink chocolate milk\n")
    (topic, payload), kw = sent[0]
    assert topic == "buddy/desk_buddy_x/memories" and kw == {"qos": 1, "retain": True}
    assert json.loads(payload) == {"version": 3, "from": "pc", "notes": "My dog is King\nI drink chocolate milk\n"}


# ---------------------------------------------------------------------------
# The Trofeo Vision screen (no USB: the pieces are checked byte by byte)


def test_trofeo_reconnects_after_the_pc_sleeps():
    from unittest import mock
    from buddy_pc import trofeo
    made, closed, started = [], [], []
    stop = threading.Event()
    clock = [1000.0]

    class Screen:
        def __init__(self, *a):
            made.append(self)

        def show(self, image):
            clock[0] += 0.05
            if len(made) == 1 and clock[0] > 1001:
                clock[0] += 3600  # the PC slept (and the screen took pictures without an error)
            if len(made) == 2:
                stop.set()

        def close(self):
            closed.append(self)

    with mock.patch.object(trofeo, "TrofeoScreen", Screen), mock.patch.object(trofeo, "Layout"), \
            mock.patch.object(trofeo, "Stats"), mock.patch.object(trofeo.time, "time", lambda: clock[0]), \
            mock.patch.object(trofeo, "start_after", lambda path, log: started.append(path)):
        stop.wait = lambda t=None: stop.is_set()
        trofeo.run(None, {"start_after_screen": "SignalRGB"}, lambda *a, **k: None, stop=stop)
    assert len(made) == 2 and closed == made[:1]
    assert started == ["SignalRGB", "SignalRGB"]  # started again if it closed when the PC slept


def test_trofeo_pieces():
    import struct as st
    from buddy_pc import trofeo
    hs = trofeo.handshake_packet()
    assert len(hs) == 2048 and hs[:16] == bytes([2, 255, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0])
    assert not any(hs[16:])
    for n in (1, 495, 496, 497, 1984, 30001):
        jpg = bytes(range(256)) * (n // 256) + bytes(range(n % 256))
        frame = trofeo.frame_chunks(jpg)
        count = n // 496 + 1
        assert len(frame) % (4 * 512) == 0 and len(frame) // 512 >= count
        got = b""
        for i in range(count):
            c = frame[i * 512:(i + 1) * 512]
            total, size = st.unpack_from("<IH", c, 2)
            cnt, idx = st.unpack_from("<HH", c, 9)
            assert c[0] == 1 and c[1] == 255 and c[8] == 1 and total == n and (cnt, idx) == (count, i)
            assert size == (496 if i < count - 1 else n % 496)
            got += c[16:16 + size]
        assert got == jpg and not any(frame[count * 512:])  # padding is zeros
        writes = list(trofeo.usb_writes(frame))
        assert b"".join(writes) == frame and all(len(w) in (4096, 2048) for w in writes)
    reply = bytearray(512)
    reply[0], reply[1], reply[8], reply[22] = 3, 255, 1, 4
    assert trofeo.parse_handshake(reply) == 5 and trofeo.angle_for(5) == 180
    assert trofeo.angle_for(3) == 0 and trofeo.angle_for(5, "0") == 0
    try:
        trofeo.parse_handshake(bytes(512))
        assert False
    except ValueError:
        pass


def test_afterburner_readings():
    import struct as st
    from buddy_pc import trofeo
    entries = [("CPU temperature", 44.0), ("CPU usage", 6.0), ("GPU temperature", 51.0), ("GPU usage", 12.0)]
    header, entry = 32, 1324
    buf = bytearray(header + entry * len(entries))
    st.pack_into("<5I", buf, 0, 0x4D41484D, 0x00020000, header, len(entries), entry)
    for i, (name, value) in enumerate(entries):
        at = header + i * entry
        buf[at:at + len(name)] = name.encode()
        st.pack_into("<f", buf, at + 1300, value)
    r = trofeo.parse_afterburner(bytes(buf))
    assert r == {"CPU temperature": 44.0, "CPU usage": 6.0, "GPU temperature": 51.0, "GPU usage": 12.0}
    assert trofeo.pick(r, "cpu temperature") == 44.0 and trofeo.pick(r, "nothing") is None
    assert trofeo.pick({"Framerate": 3.4028234663852886e38}, "Framerate") is None  # Afterburner's "no data"
    assert trofeo.pick({"Framerate": float("nan")}, "Framerate") is None
    st.pack_into("<I", buf, 0, 0xDEAD)  # Afterburner closed
    assert trofeo.parse_afterburner(bytes(buf)) == {}


def test_rivatuner_fps():
    import struct as st
    from buddy_pc import trofeo
    entry, offset = 300, 64
    buf = bytearray(offset + entry * 3)
    st.pack_into("<5I", buf, 0, 0x52545353, 0x00020000, entry, offset, 3)
    st.pack_into("<I", buf, offset + 0 * entry, 0)  # an empty slot
    for i, (pid, t0, t1, frames) in enumerate([(111, 1000, 2000, 30), (222, 9000, 10000, 144)], start=1):
        at = offset + i * entry
        st.pack_into("<I", buf, at, pid)
        st.pack_into("<3I", buf, at + 268, t0, t1, frames)
    assert round(trofeo.parse_rtss(bytes(buf), now_ms=10500)) == 144  # the game updated most recently
    assert trofeo.parse_rtss(bytes(buf), now_ms=20000) is None       # nothing recent: no game running
    st.pack_into("<I", buf, 0, 0)
    assert trofeo.parse_rtss(bytes(buf), now_ms=10500) is None       # not RivaTuner's memory


def test_start_after_screen_with_options():
    from buddy_pc import trofeo
    assert trofeo.split_command(r'"C:\\A B\\SignalRgbLauncher.exe" --silent') == (r"C:\\A B\\SignalRgbLauncher.exe", "--silent")
    assert trofeo.split_command(r"C:\\x\\App.exe -minimized") == (r"C:\\x\\App.exe", "-minimized")
    assert trofeo.split_command(r"C:\\Start Menu\\SignalRGB.lnk") == (r"C:\\Start Menu\\SignalRGB.lnk", "")


def test_program_names_for_reclaiming_the_screen():
    from buddy_pc import trofeo
    n = trofeo.program_names(r"C:\\Start Menu\\SignalRGB.lnk")
    assert {"signalrgb.exe", "signalrgblauncher.exe"} <= n
    assert trofeo.program_names(r'"C:\\x\\Other.exe" -min') == {"other.exe"}


def test_finding_a_start_menu_shortcut():
    import tempfile
    from buddy_pc import trofeo
    d = tempfile.mkdtemp()
    for f in ("SignalRGB.lnk", "Uninstall SignalRGB.lnk"):
        open(os.path.join(d, f), "w").close()
    assert trofeo.find_shortcut(os.path.join(d, "SignalRgbLauncher.exe")) == os.path.join(d, "SignalRGB.lnk")
    assert trofeo.find_shortcut(os.path.join(d, "Something.exe")) is None
    os.remove(os.path.join(d, "SignalRGB.lnk"))
    assert trofeo.find_shortcut(os.path.join(d, "SignalRgbLauncher.exe")) is None  # never the uninstaller


def test_trofeo_shows_the_face():
    import sys as _sys
    import types as _types
    from buddy_pc import trofeo
    writes, reads = [], []

    class EP:
        def __init__(self, addr):
            self.bEndpointAddress, self.bmAttributes = addr, 2  # bulk

    class Dev:
        def set_configuration(self):
            pass

        def get_active_configuration(self):
            return [[EP(0x83)], [EP(0x09), EP(0x02), EP(0x82)]]  # 0x09 is tried first and fails

        def write(self, ep, data, timeout=0):
            if ep == 0x09:  # a pair that's listed but doesn't answer
                raise OSError("no answer")
            writes.append((ep, bytes(data)))

        def read(self, ep, n, timeout=0):
            reads.append(ep)
            r = bytearray(n)
            r[0], r[1], r[8], r[22] = 3, 255, 1, 4
            return r

    usb = _types.ModuleType("usb")
    usb.core = _types.ModuleType("usb.core")
    usb.core.find = lambda **k: Dev()
    usb.core.USBError = OSError
    saved = {k: _sys.modules.get(k) for k in ("usb", "usb.core")}
    _sys.modules.update({"usb": usb, "usb.core": usb.core})
    try:
        stop = threading.Event()
        t = threading.Thread(target=trofeo.run, args=(Face(seed=2), {"trofeo_fps": 30}, lambda *a, **k: None, stop))
        t.start()
        time.sleep(1.0)
        stop.set()
        t.join(5)
    finally:
        for k, v in saved.items():
            if v is None:
                _sys.modules.pop(k, None)
            else:
                _sys.modules[k] = v
    assert writes[0][1] == trofeo.handshake_packet()
    frames = sum(1 for ep, w in writes if w[:2] == bytes([1, 255]) and w[11:13] == bytes(2))  # first pieces
    assert frames >= 5, frames  # several pictures a second
    first = next(w for ep, w in writes if w[:2] == bytes([1, 255]))
    assert first[16:18] == bytes([0xFF, 0xD8])  # a JPEG
    assert all(ep == 0x02 for ep, w in writes) and set(reads) == {0x82}  # read from the screen


# ---------------------------------------------------------------------------
# The conversation, with stand-ins for the microphone, Whisper, Ollama and Kokoro


class FakeSpeaker:
    def __init__(self):
        self.said, self.level, self.busy, self.chimes = [], 0.0, False, 0
        self.ducks = type("D", (), {"hold": lambda self, why, on: None})()

    def chime(self):
        self.chimes += 1

    def say(self, t):
        self.said.append(t)

    def end(self):
        pass

    def wait(self, timeout=None):
        return True

    def stop(self):
        pass


def _fake_buddy(replies, heard):
    import tempfile
    from buddy_pc import jokes
    from buddy_pc.app import Buddy
    jokes.FILE = os.path.join(tempfile.mkdtemp(), "jokes.json")  # not the real one

    b = Buddy.__new__(Buddy)
    b.echo_wait = 0
    b._pc_said = lambda seconds: ""
    b.cfg = {"follow_up": True, "voice_id_url": ""}
    b.state, b.reply_expr, b.linger_until = "idle", "happy", 0.0
    b.click, b.cancel = threading.Event(), threading.Event()
    b.speaker = FakeSpeaker()
    b.mic = type("M", (), {"drain": lambda self: None})()
    heard = list(heard)
    b.endpoint = type("E", (), {"start_window": 6, "hit_limit": False, "loudest": -30.0, "floor": -90.0, "record": lambda self, mic, cancelled=None, ignore_first=0, min_db=-120.0:
                                np.zeros(16000, np.int16) if heard else None})()
    b.ears = type("W", (), {"text": lambda self, pcm, hotwords=None, strict=False: heard.pop(0)})()
    replies = list(replies)
    b.brain = type("B", (), {"chat": lambda self, h, extra="", looked=False: iter(replies.pop(0))})()
    return b


def test_conversation_speaks_sentence_by_sentence_and_follows_up():
    b = _fake_buddy([["[laughing] Ha", "! That's a good one, ", "Eric. Want another?"],
                     ["[cool] Sure thing."]], ["tell me a joke", "yes"])
    b._conversation()
    assert b.speaker.said == ["Ha!", "That's a good one, Eric.", "Want another?", "Sure thing."]
    assert b.reply_expr == "cool"


def test_conversation_stops_on_a_repeated_echo():
    b = _fake_buddy([["[happy] You're welcome!"], ["[happy] Anytime!"]], ["thank you", "Thank you.", "x"])
    b._conversation()
    assert b.speaker.said == ["You're welcome!"]


def test_memory_commands():
    from buddy_pc import memory as M
    assert M.parse_command("Hey Jarvis, remember that my daughter's name is Corey.") == \
        ("remember", "my daughter's name is Corey")
    assert M.parse_command("Remember to buy milk") == (None, "")      # a reminder, not a note
    assert M.parse_command("Do you remember my dog?") == (None, "")   # a question: the AI
    assert M.parse_command("Forget that.") == ("forget_last", "")
    assert M.parse_command("forget about the dentist") == ("forget", "the dentist")
    assert M.to_second_person("I am tired and my dog likes me") == "you are tired and your dog likes you"
    notes, said, changed = M.apply("remember", "my daughter's name is Corey", ["My dog is King"])
    assert changed and notes[-1] == "my daughter's name is Corey"
    assert said == "[happy] Got it! I'll remember that your daughter's name is Corey."
    notes, said, changed = M.apply("remember", "My daughter's name is Corey!", notes)
    assert not changed and said.startswith("[winking]")
    notes, said, changed = M.apply("forget", "dog", notes)
    assert changed and notes == ["my daughter's name is Corey"]
    assert M.apply("forget", "cat", notes)[2] is False


def test_conversation_remember_goes_to_the_shared_notes():
    import tempfile
    from buddy_pc.shared import SharedNotes
    b = _fake_buddy([], ["remember that I drink chocolate milk"])
    shared = SharedNotes({}, lambda *a, **k: None, cache_file=os.path.join(tempfile.mkdtemp(), "m.json"))
    shared.apply(json.dumps({"version": 4, "from": "desk", "notes": "My dog is King\n"}), "buddy/x/memories")
    sent = []
    shared.client = type("C", (), {"publish": lambda self, *a, **k: sent.append(a)})()
    b.brain.shared = shared
    b._conversation()
    assert b.speaker.said == ["Got it!", "I'll remember that you drink chocolate milk."]
    assert shared.notes() == ["My dog is King", "I drink chocolate milk"] and shared.version == 5
    assert json.loads(sent[0][1])["version"] == 5


def test_wake_phrase_matching():
    from buddy_pc.hearing import is_phrase_wake_word, match_phrase, strip_phrase
    assert is_phrase_wake_word("hey buddy") and is_phrase_wake_word("hey_buddy")
    assert not is_phrase_wake_word("hey_jarvis") and not is_phrase_wake_word("hey_buddy.onnx")
    for yes in ("Hey Buddy.", "Hey, buddy!", "Hi buddy", "A buddy, are you there?", "hey body", "Heybuddy what's up", "Okay hey buddy",
                "Hey buddy, what time is it?"):
        assert match_phrase("hey buddy", yes) >= 0, yes
    for no in ("Hey everybody, welcome back to the channel", "That's my buddy over there", "", "Hi there"):
        assert match_phrase("hey buddy", no) < 0, no
    assert match_phrase("hey buddy", "Hey Judy, watch this", strict=True) < 0     # the video's "Judy"
    assert match_phrase("hey buddy", "Hey Buddy, tell me a joke", strict=True) >= 0
    assert strip_phrase("hey buddy", "Hey, Buddy, what time is it?") == "what time is it?"
    assert strip_phrase("hey buddy", "Hey Buddy.") == ""
    assert strip_phrase("hey buddy", "What time is it?") == "What time is it?"


def test_conversation_with_the_question_said_with_the_wake_word():
    b = _fake_buddy([["[happy] It's noon."]], ["Hey Buddy, what time is it?"])
    b.wake = type("W", (), {"phrase": "hey buddy"})()
    b.cfg["follow_up"] = False
    b._conversation(np.zeros(16000, np.int16))
    assert b.speaker.said == ["It's noon."]


def test_learn_my_voice_on_the_pc():
    from unittest import mock
    from buddy_pc import voiceid
    assert voiceid.is_enroll_request("Hey Buddy, learn my voice.")
    assert voiceid.is_enroll_request("Can you remember my voice?")
    b = _fake_buddy([], ["learn my voice"])
    b.cfg["voice_id_url"] = "http://x"
    samples = [None, np.zeros(16000, np.int16), np.zeros(16000 * 4, np.int16), np.zeros(16000 * 5, np.int16),
               np.zeros(16000 * 6, np.int16)]
    first = [True]

    def record(mic, cancelled=None, ignore_first=0, min_db=-120.0):
        if first[0]:
            first[0] = False
            return np.zeros(16000, np.int16)  # "learn my voice" itself
        return samples.pop(0)

    b.endpoint.record = record
    b.endpoint.end_silence = 0.9
    sent = []
    with mock.patch.object(voiceid, "enroll", lambda cfg, pcm: sent.append(len(pcm)) or len(sent) + 3), \
            mock.patch.object(voiceid, "identify", lambda *a: "unsure"):
        b._conversation()
    assert sent == [16000 * 4, 16000 * 5, 16000 * 6]
    said = " ".join(b.speaker.said)
    assert "I didn't catch that" in said and "That was a bit short" in said and "All done!" in said
    assert b.endpoint.end_silence == 0.9  # put back


def test_question_said_right_after_hey_buddy_is_kept():
    # "Hey Buddy learn my voice" in one breath, a bit quieter than he expected:
    # what came in while he chimed isn't thrown away, and the bar follows how
    # loud the "Hey Buddy" was.
    b = _fake_buddy([["[happy] Hi."]], ["hello"])
    b.cfg["follow_up"] = False
    drained, calls = [], []
    b.mic = type("M", (), {"drain": lambda self: drained.append(1)})()
    b.endpoint.record = lambda mic, cancelled=None, ignore_first=0, min_db=-120.0: \
        calls.append((ignore_first, min_db)) or np.zeros(16000, np.int16)
    b.your_level = -38.0  # (as after a "Hey Buddy" at about -40 dB)
    b.just_woke = True
    b._pc_quiet = lambda seconds: True  # (with a video playing, that would be the video: drained)
    b._conversation()
    assert drained == [] and calls[0][0] == 0.0 and calls[0][1] <= -50
    assert not b.just_woke


def test_no_remarks_to_an_empty_room():
    from unittest import mock
    from buddy_pc import presence as P
    b = _fake_buddy([], [])
    b.cfg["need_to_see_you"] = True
    b.remarks = queue.Queue()
    b.brain.history = []
    for where in ("away", "turned"):
        b.remarks.put((time.time(), "[silly] I'd take over the world."))
        with mock.patch.object(P, "check", lambda cfg, log: where):
            assert b._say_remark()
    assert b.speaker.said == ["I'd take over the world."]  # only when you were there (turned is fine)


def test_listens_again_when_it_caught_the_video_first():
    # "Hey Buddy" over a video: what he caught next was the video (before it
    # paused). He listens again for the question instead of giving up.
    b = _fake_buddy([["[laughing] Here's one."]], ["No matter what shape, size or era", "tell me a joke"])
    b.cfg["follow_up"] = False
    played = iter(["No matter what shape, size, or era, this concept", ""])
    b._pc_said = lambda seconds: next(played, "")
    b._conversation()
    assert b.speaker.said == ["Here's one."]


def test_facing_him_or_not():
    from unittest import mock
    from buddy_pc import presence as P
    lit = np.full((48, 64, 3), 120, np.uint8)
    with mock.patch.object(P, "faces", lambda f: []):
        assert P.judge(lit) == "away"
        assert P.judge(lit * 0) is None  # dark: can't tell, so it doesn't count against you
        assert P.judge(None) is None  # no camera
    with mock.patch.object(P, "faces", lambda f: [(0, 0, 50, 60, 0.8, 0.9)]):
        assert P.judge(lit) == "turned"
    with mock.patch.object(P, "faces", lambda f: [(0, 0, 50, 60, 0.8, 0.9), (0, 0, 50, 60, 0.1, 0.9)]):
        assert P.judge(lit) == "facing"


def test_follow_up_ignored_when_youre_turned_away():
    b = _fake_buddy([["[happy] Hi."], ["[happy] Sure."]], ["hello", "and another thing"])
    class Turned:
        value = "turned"

        def result(self, timeout=3.0):
            return self.value

    b._look = Turned  # (only asked for the follow-up: the wake word was checked already)
    b._conversation()
    assert b.speaker.said == ["Hi."]


def test_vision_questions_look_first():
    from unittest import mock
    from buddy_pc import tools
    assert T.is_vision_request("Hey Buddy, what is this in my hand?")
    assert T.is_vision_request("How many fingers am I holding up?")
    assert not T.is_vision_request("What's the weather?")
    b = _fake_buddy([["[happy] An iPhone!"]], ["what am I holding?"])
    b.cfg.update({"vision_model": "v", "follow_up": False})
    seen = {}

    def chat(h, extra="", looked=False):
        seen["extra"], seen["looked"] = extra, looked
        return iter(["[happy] An iPhone!"])

    b.brain = type("B", (), {})()
    b.brain.chat = chat
    sent = []
    b.brain.shared = type("S", (), {"saw": lambda self, t: sent.append(t)})()
    b.cfg["owner"] = "Eric"
    with mock.patch.object(tools, "look", lambda cfg, q, log: "Through your camera you see: a black iPhone."):
        b._conversation()
    assert seen["looked"] and "a black iPhone" in seen["extra"]
    assert sent == ["a black iPhone"]  # for Buddy's eyes' diary
    assert b.speaker.said == ["An iPhone!"]


def test_diary_line_from_a_look():
    from buddy_pc.app import diary_line
    seen = ("Through your camera you see: You are holding an iPhone with the back facing up. It has a red case.")
    assert diary_line(seen, "Eric") == "Eric is holding an iPhone with the back facing up"
    assert diary_line("Through your camera you see: I can't tell what that is.", "Eric") == ""


def test_false_wake_is_dropped():
    b = _fake_buddy([["[happy] Hi!"]], ["Okay, let me see"])
    b.wake = type("W", (), {"phrase": "hey buddy"})()
    b._conversation(np.zeros(16000, np.int16))
    assert b.speaker.said == []


def test_jokes_differ():
    import tempfile
    from unittest import mock
    from buddy_pc import jokes
    with mock.patch.object(jokes, "FILE", os.path.join(tempfile.mkdtemp(), "j.json")):
        assert jokes.wanted("Hey Buddy, tell me a joke.") and not jokes.wanted("what's the weather?")
        jokes.told("Why don't skeletons fight? No guts!")
        h = jokes.hint("pirates")
        assert "about pirates" in h and "Why don't skeletons fight? No guts!" in h


def test_follow_up_from_a_video_is_ignored():
    from unittest import mock
    from buddy_pc import voiceid
    b = _fake_buddy([["[happy] Ha!"], ["[happy] Sure!"]], ["tell me a joke", "now we're even a conversation"])
    b.cfg["voice_id_url"] = "http://x"

    def ident(cfg, pcm, log):
        voiceid.LAST.update(decision="other", score=0.1)
        return "other"

    with mock.patch.object(voiceid, "identify", ident):
        b._conversation()
    assert b.speaker.said == ["Ha!"]  # the video's "follow-up" got no answer


def test_hey_buddy_in_the_middle_of_a_video():
    from buddy_pc.app import Buddy
    from buddy_pc.hearing import Endpointer
    even = lambda f: (f & ~1).astype(np.int16)
    video = [even(_tone(-20)) for _ in range(75)]                 # 6 s of nonstop talk (even samples)
    hey = [even(_tone(-18)) | 1 for _ in range(12)]               # "Hey Buddy" (marked: odd samples)
    after = [even(_tone(-34)) for _ in range(10)] + [even(_tone(-70)) for _ in range(20)]  # video turned down
    b = Buddy.__new__(Buddy)
    b.cfg = {}
    b.click, b.cancel = threading.Event(), threading.Event()
    b.speaker = FakeSpeaker()
    b.remarks = __import__("queue").Queue()
    b.mic = FakeMic(video + hey + after)
    b.endpoint = Endpointer()
    checks = []

    class Wake:
        phrase = "hey buddy"

        def check(self, audio):
            checks.append(len(audio))
            return bool(np.any(audio % 2)), "hey buddy" if np.any(audio % 2) else "video talk"

        def reset(self):
            pass

    b.wake = Wake()
    b.ears = type("E", (), {"text": lambda self, a, hotwords=None, strict=False:
                            "Hey Buddy" if np.any(a % 2) else "video talk"})()
    pcm = b._idle_phrase()
    assert len(checks) >= 4                     # kept checking while the video talked
    assert pcm is not None and np.any(pcm % 2)  # caught it, and kept the "Hey Buddy" part
    assert len(pcm) < 60 * 1280                 # without most of the video from before it


def test_video_chatter_that_fools_the_quick_check_doesnt_wake_him():
    from buddy_pc.app import Buddy
    from buddy_pc.hearing import Endpointer
    frames = [_tone(-18) for _ in range(40)] + [_tone(-70) for _ in range(15)]
    b = Buddy.__new__(Buddy)
    b.cfg = {}
    b.click, b.cancel = threading.Event(), threading.Event()
    b.speaker = FakeSpeaker()
    b.remarks = __import__("queue").Queue()
    b.mic = FakeMic(frames)
    b.endpoint = Endpointer()
    b.wake = type("W", (), {"phrase": "hey buddy", "check": lambda self, a: (True, "hey buddy"),
                            "reset": lambda self: None})()
    b.ears = type("E", (), {"text": lambda self, a, hotwords=None, strict=False: "Hey Judy, watch this"})()
    done = {}
    t = threading.Thread(target=lambda: done.setdefault("r", b._idle_phrase()), daemon=True)
    t.start()
    t.join(8)
    assert "r" not in done  # still waiting: the careful check didn't hear "Hey Buddy"


def test_media_app_names():
    from buddy_pc.media import app_name
    assert app_name("Microsoft.MicrosoftEdge.Stable_8wekyb3d8bbwe!App") == "Microsoft Edge"
    assert app_name("MSEdge") == "Microsoft Edge" and app_name("Spotify.exe") == "Spotify"
    assert app_name("Publisher.CoolPlayer_abc123!App") == "CoolPlayer"
    assert app_name("com-54E21B02") == ""  # Edge's code for a site: no name


def test_screen_questions_and_window_names():
    from buddy_pc import pcinfo
    assert T.is_screen_request("What's this error?") and T.is_screen_request("what's on my screen")
    assert T.is_screen_request("can you read this page for me")
    assert not T.is_screen_request("what am I holding?") and not T.is_screen_request("tell me a joke")
    assert pcinfo.clean_title("Good Mythical Morning - YouTube - Personal - Microsoft\u200b Edge") == \
        "Good Mythical Morning - YouTube"
    assert pcinfo.app_label("msedge.exe") == "Microsoft Edge" and pcinfo.app_label("Game.exe") == "Game"


def test_media_pauses_what_plays_and_resumes_it():
    import sys as _sys
    import types as _types
    from enum import IntEnum
    from unittest import mock

    class St(IntEnum):
        PLAYING = 4
        PAUSED = 5

    calls = []

    class Session:
        def __init__(self, app, playing):
            self.source_app_user_model_id, self.status = app, St.PLAYING if playing else St.PAUSED

        def get_playback_info(self):
            return _types.SimpleNamespace(playback_status=self.status)

        async def try_pause_async(self):
            calls.append(("pause", self.source_app_user_model_id))
            self.status = St.PAUSED
            return True

        async def try_play_async(self):
            calls.append(("play", self.source_app_user_model_id))
            self.status = St.PLAYING
            return True

        async def try_get_media_properties_async(self):
            return _types.SimpleNamespace(title="Good Mythical Morning", artist="")

    sessions = [Session("MSEdge", True), Session("Spotify.exe", False)]

    class Manager:
        @staticmethod
        async def request_async():
            return _types.SimpleNamespace(get_sessions=lambda: sessions)

    ctl = _types.ModuleType("winrt.windows.media.control")
    ctl.GlobalSystemMediaTransportControlsSessionManager = Manager
    ctl.GlobalSystemMediaTransportControlsSessionPlaybackStatus = St
    mods = {"winrt": _types.ModuleType("winrt"), "winrt.windows": _types.ModuleType("winrt.windows"),
            "winrt.windows.media": _types.ModuleType("winrt.windows.media"), "winrt.windows.media.control": ctl}
    with mock.patch.dict(_sys.modules, mods):
        from buddy_pc.media import Media
        m = Media(lambda *a, **k: None)
        assert m.now_playing() == '"Good Mythical Morning" (Microsoft Edge)'
        assert m.pause()
        assert m.now_playing() == '"Good Mythical Morning" (Microsoft Edge)'  # paused by him: still "playing"
        m.resume()
    assert calls == [("pause", "MSEdge"), ("play", "MSEdge")]  # Spotify was already paused: left alone


def test_what_the_pc_played_isnt_you():
    from buddy_pc.loopback import same_words
    assert same_words("Oklahoma is in Oklahoma City or Tulsa", "is Oklahoma in Oklahoma City or is it Tulsa, Rhett")
    assert not same_words("Hey Buddy, tell me a joke", "Oklahoma is in Oklahoma City or Tulsa")
    assert not same_words("Hi", "Hi there")  # too short to tell
    b = _fake_buddy([["[happy] It's Oklahoma City!"]], ["Oklahoma is in Oklahoma City or Tulsa"])
    b._pc_said = lambda seconds: "is Oklahoma in Oklahoma City or is it Tulsa"
    b._conversation()
    assert b.speaker.said == []  # the video asked, not you


def test_conversation_cancel_and_clarify_end_it():
    b = _fake_buddy([], ["never mind"])
    b._conversation()
    assert b.speaker.said == []
    b = _fake_buddy([["[confused] I didn't catch that, could you say it again?"]], ["blah blah", "more video"])
    b._conversation()
    assert len(b.speaker.said) == 1  # no follow-up after "say it again?"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, repr(e))
    sys.exit(1 if failed else 0)
