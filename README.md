# Buddy on the PC

The desk Buddy's second body: his face in a small always-on-top window on
your PC, listening and talking back, all on your own machine. It uses the
same Ollama models and the same Kokoro voice as the desk Buddy, shares his
memory notes, and knows what Buddy's eyes saw today. Skipping the ESP32 and
Home Assistant in between makes it faster: Whisper runs on the graphics
card, and he starts talking as soon as the first sentence of his answer is
written.

The desk Buddy lives in
[ecstoneusps/Claude-gadget](https://github.com/ecstoneusps/Claude-gadget).

## Talking to him

- Say **"Hey Jarvis"** (for now; a trained "Hey Buddy" comes later), or
  **click his face**, or press the **space bar** with his window selected.
  His eyes go big and green: he's listening.
- He thinks (eyes up and to the side), then answers out loud with his face
  showing the mood of the answer and his mouth moving with his voice.
- After answering he keeps listening for about 5 seconds, so you can carry
  on without the wake word. He doesn't when he only asked you to repeat
  yourself (a video he misheard, say).
- **Click his face** while he's listening, thinking or talking to cancel. Saying
  "cancel", "never mind" or "I wasn't talking to you" works too.
- Right-click his face for **Always on top** and **Quit**.

## What runs where (all on this PC)

| Part | What | Notes |
|---|---|---|
| Ears | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) `large-v3-turbo` | On the NVIDIA card; falls back to the processor (with the smaller `small` model) if the card can't be used |
| Wake word | [openWakeWord](https://github.com/dscripka/openWakeWord) | `hey_jarvis` until a "Hey Buddy" model is trained |
| Brain | Ollama, `qwen3:4b-instruct` | `personality.txt` is his personality; the desk Buddy's notes and today's diary are added |
| Voice | Kokoro (the `kokoro-wyoming` Docker container Home Assistant already uses, port 10210) | Same voice as the desk Buddy |

## Setup (Windows)

1. **Python 3.10 to 3.13** (3.12 is best; 3.14 and newer are too new for some
   of the parts he needs). If you don't have one: in PowerShell,
   `py install 3.12` (or `winget install Python.Python.3.12`), then close and
   reopen PowerShell.
2. **Get the files**: download this repository as a ZIP (green **Code**
   button, **Download ZIP**) and extract it, for example to `C:\BuddyPC`.
3. **Settings**: copy `buddy.example.ini` to `buddy.ini` and open it in
   Notepad (Windows may hide the `.ini`; it should look like the example's
   name without "example"). Check your name and the voice (the same one the
   desk Buddy uses in Home Assistant), and fill in:
   - `desk_buddy_url`: the desk Buddy's address, the one his settings page is
     on (e.g. `http://192.168.4.50`), so both bodies share his memory notes.
   - `eyes_diary`: the full path to Buddy's eyes' `diary.json`.
4. **Start**: double-click **run-buddy.bat**. The first start sets everything
   up (a few minutes) and downloads the Whisper model (about 1.6 GB) and the
   wake word models, so give it time. Later starts take a few seconds.

The window shows a sleepy face while he's starting, then his normal face.
The black console window shows what he hears and says.

## Personality

`personality.txt` is his personality and instructions; edit it freely (`{owner}`
becomes your name). To make him the same as the desk Buddy, paste in the
instructions from Home Assistant (Settings → Devices & services → Ollama →
Buddy brain → Configure → Instructions), but keep the line about starting
each reply with an emotion tag. His memory notes and the diary are added
automatically, so leave the Home Assistant template lines out.

## Coming next

- Coordination with the desk Buddy over MQTT, so only one answers, then a
  trained "Hey Buddy" wake word.
- "Remember that..." from the PC, saved into the desk Buddy's notes.
- Web search (SearXNG), seeing through the webcam, and voice ID.

## Tests

`python tests/test_buddy.py` (no microphone, speakers, GPU, Ollama or Kokoro
needed: those are stood in for).
