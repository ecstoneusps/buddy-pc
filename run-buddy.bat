@echo off
cd /d "%~dp0"
rem Buddy on the PC. The first start sets everything up (a few minutes).
if not exist .venv\Scripts\python.exe (
  echo Setting up Buddy for the first time...
  rem 3.12 is best; 3.13, 3.11 and 3.10 work too (3.14+ is too new for some parts).
  for %%v in (3.12 3.13 3.11 3.10) do if not exist .venv\Scripts\python.exe (
    py -%%v -m venv .venv >nul 2>&1 && echo Using Python %%v
  )
)
if not exist .venv\Scripts\python.exe (
  echo Buddy needs Python 3.10 to 3.13. Install 3.12 with:  py install 3.12
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
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
set PYTHONWARNINGS=ignore::UserWarning
.venv\Scripts\python.exe -m buddy_pc
pause
