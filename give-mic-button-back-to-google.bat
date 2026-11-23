@echo off
rem Switches Google's Assistant back on: the microphone button is Google's again.
setlocal
cd /d "%~dp0"
set ADB=platform-tools\adb.exe
if not exist %ADB% goto noadb
if not exist tv-address.txt goto noadb
set /p TV=<tv-address.txt
%ADB% connect %TV% | find "connected to" >nul
if errorlevel 1 goto noadb
%ADB% -s %TV% shell pm enable com.google.android.katniss
echo Done: Google Assistant is back on.
pause
exit /b 0

:noadb
echo Can't reach the TV: run install-tv-app.bat first (it connects to the TV).
pause
exit /b 1
