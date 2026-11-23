@echo off
rem Installs the Buddy app on the Google TV, from this PC, over the network.
rem Before this: on the TV, turn on Developer options and USB debugging (README).
setlocal
cd /d "%~dp0"

if not exist BuddyTV.apk (
  echo Put BuddyTV.apk ^(from the buddy-pc Releases page, "Buddy TV app"^) in this folder first.
  pause
  exit /b 1
)

rem adb: Google's tool for talking to Android devices, downloaded once.
if not exist platform-tools\adb.exe (
  echo Getting adb from Google, once...
  powershell -NoProfile -Command "Invoke-WebRequest https://dl.google.com/android/repository/platform-tools-latest-windows.zip -OutFile platform-tools.zip; Expand-Archive -Force platform-tools.zip .; Remove-Item platform-tools.zip"
)
if not exist platform-tools\adb.exe (
  echo Couldn't get adb. Check the internet connection and try again.
  pause
  exit /b 1
)
set ADB=platform-tools\adb.exe

set /p TVIP=The TV's IP address (TV Settings, Network, your network): 
%ADB% connect %TVIP%:5555
echo.
echo If the TV asks "Allow USB debugging?", choose Always allow, then press a key here.
pause >nul
%ADB% connect %TVIP%:5555

echo Installing Buddy...
%ADB% -s %TVIP%:5555 install -r BuddyTV.apk
if errorlevel 1 (
  echo Installing failed: see the message above.
  pause
  exit /b 1
)
rem Let him float over everything (the Apple TV's input too), and use the TV's microphone.
%ADB% -s %TVIP%:5555 shell appops set com.buddy.tv SYSTEM_ALERT_WINDOW allow
%ADB% -s %TVIP%:5555 shell pm grant com.buddy.tv android.permission.RECORD_AUDIO

set /p BUDDYURL=Buddy's address, the one your phone uses (like absdesktop.lemur-copperhead.ts.net): 
%ADB% -s %TVIP%:5555 shell am start -n com.buddy.tv/.MainActivity --es url "%BUDDYURL%"

echo.
echo Done: Buddy should be on the TV now.
pause
