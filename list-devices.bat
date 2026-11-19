@echo off
cd /d "%~dp0"
rem Lists the microphones and speakers, for microphone = and speaker = in buddy.ini.
if not exist .venv\Scripts\python.exe (
  echo Run run-buddy.bat once first, from this same folder: %~dp0
  echo ^(it sets up the .venv folder this needs^).
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m buddy_pc.devices
pause
