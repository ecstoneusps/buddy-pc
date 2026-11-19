@echo off
cd /d "%~dp0"
rem Lists the microphones and speakers, for microphone = and speaker = in buddy.ini.
.venv\Scripts\python.exe -c "import sounddevice as sd; print(sd.query_devices()); print(); print(\"> = Windows default input, < = default output\")"
pause
