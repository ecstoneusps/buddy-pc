@echo off
rem The remote's microphone button talks to Buddy instead of Google's Assistant.
rem It switches the Assistant app off on the TV (nothing is deleted; to undo,
rem run give-mic-button-back-to-google.bat). Run install-tv-app.bat first.
setlocal
cd /d "%~dp0"
set ADB=platform-tools\adb.exe
if not exist %ADB% goto noadb
if not exist tv-address.txt goto noadb
set /p TV=<tv-address.txt
%ADB% connect %TV% | find "connected to" >nul
if errorlevel 1 goto noadb

echo This switches off Google Assistant on the TV ("Hey Google", voice search),
echo so the microphone button can start Buddy. It can be switched back on.
choice /m "Go ahead"
if errorlevel 2 exit /b 0
%ADB% -s %TV% shell pm disable-user --user 0 com.google.android.katniss
echo.
echo Done. Press the microphone button: if Android asks which app to use,
echo choose "Talk to Buddy" and "Always".
pause
exit /b 0

:noadb
echo Can't reach the TV: run install-tv-app.bat first (it connects to the TV).
pause
exit /b 1
