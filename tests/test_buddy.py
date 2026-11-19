"""Tests that run without a microphone, speakers, GPU, Ollama or Kokoro:
python -m pytest tests  (or python tests/test_buddy.py)"""

import json
import os
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


def test_level_of():
    assert voice.level_of(np.zeros(320, dtype=np.int16)) == 0.0
    assert voice.level_of(_tone(-12, 320)) > 0.9


# ---------------------------------------------------------------------------
# Brain: a fake Ollama and a fake desk Buddy


def test_brain_streams_and_uses_notes(tmp_path=None):
    import tempfile
    from buddy_pc.brain import Brain

    seen = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # the desk Buddy's /memories
            body = b"# notes\nMy dog is King\n"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

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
           "desk_buddy_url": url, "eyes_diary": ""}
    b = Brain(cfg, lambda *a, **k: None)
    b.notes()  # starts fetching the desk Buddy's notes in the background
    for _ in range(50):
        if b._notes:
            break
        time.sleep(0.05)
    reply = "".join(b.chat("hello"))
    srv.shutdown()
    assert reply == "[happy] Hi Eric!"
    system = seen["body"]["messages"][0]["content"]
    assert "Your owner's dog is King" in system and "Eric's companion" in system
    assert seen["body"]["messages"][-1] == {"role": "user", "content": "hello"}
    assert [h[1] for h in b.history] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# The conversation, with stand-ins for the microphone, Whisper, Ollama and Kokoro


class FakeSpeaker:
    def __init__(self):
        self.said, self.level, self.busy = [], 0.0, False

    def say(self, t):
        self.said.append(t)

    def end(self):
        pass

    def wait(self, timeout=None):
        return True

    def stop(self):
        pass


def _fake_buddy(replies, heard):
    from buddy_pc.app import Buddy

    b = Buddy.__new__(Buddy)
    b.echo_wait = 0
    b.cfg = {"follow_up": True}
    b.state, b.reply_expr, b.linger_until = "idle", "happy", 0.0
    b.click, b.cancel = threading.Event(), threading.Event()
    b.speaker = FakeSpeaker()
    b.mic = type("M", (), {"drain": lambda self: None})()
    heard = list(heard)
    b.endpoint = type("E", (), {"start_window": 6, "record": lambda self, mic, cancelled=None, ignore_first=0:
                                np.zeros(16000, np.int16) if heard else None})()
    b.ears = type("W", (), {"text": lambda self, pcm: heard.pop(0)})()
    replies = list(replies)
    b.brain = type("B", (), {"chat": lambda self, h: iter(replies.pop(0))})()
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
