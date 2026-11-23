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
set TV=%TVIP%:5555
%ADB% connect %TV% | find "connected to" >nul
if not errorlevel 1 goto connected

rem Newer TVs use "Wireless debugging": pair once with a code, then connect.
echo.
echo On the TV: Settings, System, Developer options, Wireless debugging,
echo "Pair device with pairing code". It shows an IP address:port and a code.
set /p PAIRADDR=The IP address:port shown under the pairing code: 
set /p PAIRCODE=The pairing code: 
%ADB% pair %PAIRADDR% %PAIRCODE%
echo.
echo Now the IP address:port shown at the top of the Wireless debugging screen
set /p TV=(not the pairing one): 
%ADB% connect %TV% | find "connected to" >nul
if errorlevel 1 (
  echo Couldn't connect to the TV. Check that the PC and TV are on the same network.
  pause
  exit /b 1
)

:connected
echo Connected to the TV. If it asks "Allow debugging?", choose Always allow.

echo Installing Buddy...
%ADB% -s %TV% install -r BuddyTV.apk
if errorlevel 1 (
  echo Installing failed: see the message above.
  pause
  exit /b 1
)
rem Let him float over everything (the Apple TV's input too), and use the TV's microphone.
%ADB% -s %TV% shell appops set com.buddy.tv SYSTEM_ALERT_WINDOW allow
%ADB% -s %TV% shell pm grant com.buddy.tv android.permission.RECORD_AUDIO

set /p BUDDYURL=Buddy's address, the one your phone uses (like absdesktop.lemur-copperhead.ts.net): 
%ADB% -s %TV% shell am start -n com.buddy.tv/.MainActivity --es url "%BUDDYURL%"

echo.
echo Done: Buddy should be on the TV now.
pause
