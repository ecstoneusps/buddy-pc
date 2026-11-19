@echo off
cd /d "%~dp0"
rem Lists the microphones and speakers, for microphone = and speaker = in buddy.ini.
if not exist .venv\Scripts\python.exe (
  echo Run run-buddy.bat once first, from this same folder: %~dp0
  echo ^(it sets up the .venv folder this needs^).
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import sounddevice as sd; print(sd.query_devices()); print(); print(\"> = Windows default input, < = default output\")"
pause
