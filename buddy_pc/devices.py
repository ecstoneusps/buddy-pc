"""Lists the microphones and speakers (list-devices.bat), for microphone =
and speaker = in buddy.ini."""

import sounddevice as sd

apis = sd.query_hostapis()
default_in, default_out = sd.default.device
print("Microphones:")
for i, d in enumerate(sd.query_devices()):
    if d["max_input_channels"] > 0 and apis[d["hostapi"]]["name"] == "MME":
        print(f"  {'*' if i == default_in else ' '} {d['name']}")
print("\nSpeakers:")
for i, d in enumerate(sd.query_devices()):
    if d["max_output_channels"] > 0 and apis[d["hostapi"]]["name"] == "MME":
        print(f"  {'*' if i == default_out else ' '} {d['name']}")
print("\n* = Windows' default. Names can be cut short here; any part of one works in buddy.ini.")
