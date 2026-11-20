@echo off
rem Starts Buddy on the PC when you log in to Windows: a shortcut in your
rem Startup folder, to run-buddy-quiet.bat (no console; his tray icon quits him).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\Buddy on the PC.lnk'); $s.TargetPath = '%~dp0run-buddy-quiet.bat'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save()"
if errorlevel 1 (
  echo Couldn't make the shortcut.
) else (
  echo Done: Buddy starts when you log in. To stop that, run remove-from-startup.bat.
)
pause
