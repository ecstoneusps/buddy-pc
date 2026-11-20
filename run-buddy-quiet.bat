@echo off
cd /d "%~dp0"
rem Buddy on the PC with no console window: his log goes to buddy.log, and
rem his tray icon by the clock shows his window or quits him.
rem Sets up or updates what he needs first (in this window), like run-buddy.bat.
if not exist .venv\Scripts\pythonw.exe goto setup
fc /b requirements.txt .venv\installed.txt >nul 2>&1
if errorlevel 1 goto setup
goto start
:setup
echo Setting up or updating Buddy first; this window closes when he starts.
if not exist .venv\Scripts\python.exe (
  for %%v in (3.12 3.13 3.11 3.10) do if not exist .venv\Scripts\python.exe (
    py -%%v -m venv .venv >nul 2>&1
  )
)
if not exist .venv\Scripts\python.exe (
  echo Buddy needs Python 3.10 to 3.13. Install 3.12 with:  py install 3.12
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install -q --upgrade pip
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Installing failed: see the messages above.
  pause
  exit /b 1
)
copy /y requirements.txt .venv\installed.txt >nul
:start
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set HF_HUB_VERBOSITY=error
set PYTHONWARNINGS=ignore::UserWarning
start "" .venv\Scripts\pythonw.exe -m buddy_pc
