@echo off
cd /d "%~dp0"
rem Buddy on the PC. The first start sets everything up (a few minutes).
if not exist .venv\Scripts\python.exe (
  echo Setting up Buddy for the first time...
  py -3.12 -m venv .venv 2>nul || py -3.13 -m venv .venv 2>nul
)
if not exist .venv\Scripts\python.exe (
  echo Buddy needs Python 3.12. Install it with:  winget install Python.Python.3.12
  pause
  exit /b 1
)
rem Install (or update) the parts he needs when requirements.txt is new or changed.
fc /b requirements.txt .venv\installed.txt >nul 2>&1
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Installing failed: see the messages above.
    pause
    exit /b 1
  )
  copy /y requirements.txt .venv\installed.txt >nul
)
.venv\Scripts\python.exe -m buddy_pc
pause
