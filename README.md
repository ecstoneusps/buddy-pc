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

- Say **"Hey Buddy"** (`wake_word = hey buddy`), or **click his face**, or
  press the **space bar** with his window selected. You can say the question
  in the same breath: "Hey Buddy, what time is it?"
  His eyes go big and green: he's listening.
- He thinks (eyes up and to the side), then answers out loud with his face
  showing the mood of the answer and his mouth moving with his voice.
- After answering he keeps listening for about 5 seconds, so you can carry
  on without the wake word. He doesn't when he only asked you to repeat
  yourself (a video he misheard, say).
- **Click his face** while he's listening, thinking or talking to cancel. Saying
  "cancel", "never mind" or "I wasn't talking to you" works too.
- **"Remember that..."** ("remember that my daughter's name is Corey"),
  **"forget that"** (the last note), **"forget about ..."** and **"forget
  everything"** (a backup is kept in `memories-backup.txt`) work like on the
  desk Buddy, on the notes both bodies share.
- He can **search the web** (your SearXNG), **check the weather** (Open-Meteo,
  for `weather_place`) and **look through the webcam** ("what am I holding?",
  "how many fingers?") on his own when a question needs it.
- While he's running, **Buddy's eyes' remarks** and thoughts come from him
  instead of the desk Buddy (he says them when he isn't in a conversation).
- With **voice ID** (`voice_id_url`, the desk Buddy's service), he knows
  whether it's you or a guest talking (the console shows `voice: owner (score ...)`).
- Right-click his face for **Always on top** and **Quit**.

## What runs where (all on this PC)

| Part | What | Notes |
|---|---|---|
| Ears | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) `large-v3-turbo` | On the NVIDIA card; falls back to the processor (with the smaller `small` model) if the card can't be used |
| Wake word | Whisper `tiny.en` checks the start of anything said for "hey buddy" (no training); [openWakeWord](https://github.com/dscripka/openWakeWord) models (`hey_jarvis`, or a trained `.onnx`) work too | Silero's voice detector decides when someone is talking |
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
   - `mqtt_host`, `mqtt_user`, `mqtt_password`: Home Assistant's MQTT broker
     and login, the same as the desk Buddy's and Buddy's eyes', so both bodies
     share his memory notes.
   - `eyes_diary`: the full path to Buddy's eyes' `diary.json`.
4. **Start**: double-click **run-buddy.bat**. The first start sets everything
   up (a few minutes) and downloads the Whisper model (about 1.6 GB) and the
   wake word models, so give it time. Later starts take a few seconds.

5. **Start with Windows** (optional): double-click **start-with-windows.bat**
   once. He then starts (minimized) whenever you log in;
   **remove-from-startup.bat** undoes it. If Ollama or Kokoro start a little
   later than him, his first answer may take a moment; he keeps trying.

The window shows a sleepy face while he's starting, then his normal face.
The console shows which microphone he's using. If it's the wrong one (or he
can't hear you), double-click **list-devices.bat**, find your microphone in the
list, and put part of its name in `microphone =` in `buddy.ini` (the same for
`speaker =`).
The black console window shows what he hears and says.

## Personality

`personality.txt` is his personality and instructions; edit it freely (`{owner}`
becomes your name). To make him the same as the desk Buddy, paste in the
instructions from Home Assistant (Settings → Devices & services → Ollama →
Buddy brain → Configure → Instructions), but keep the line about starting
each reply with an emotion tag. His memory notes and the diary are added
automatically, so leave the Home Assistant template lines out.

## Shared memory

Buddy's memory notes ("remember that my dog's name is King") are shared by
both bodies. The shared copy is a retained message on Home Assistant's MQTT
broker, so it's there even when the other body is off: the desk Buddy picks up
changes when he starts, and the PC Buddy keeps a copy in `memories.json` for
when the broker can't be reached. (Needs desk Buddy firmware from build 90 on.)

## Who answers "Hey Buddy"

While the PC Buddy is running and listening for "Hey Buddy", he answers it
and the desk Buddy stays quiet (desk Buddy firmware from build 91 on). He
tells the desk Buddy over MQTT (`buddy_pc/status`, `buddy_pc/wake_word`);
when you quit him, or the PC goes to sleep, the desk Buddy answers again
(within about a minute if the PC just disappears). While the PC Buddy listens
for "Hey Jarvis", the desk Buddy keeps answering "Hey Buddy".

## Tests

`python tests/test_buddy.py` (no microphone, speakers, GPU, Ollama or Kokoro
needed: those are stood in for).
