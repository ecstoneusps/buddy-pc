@echo off
rem Stops Buddy on the PC from starting when you log in.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Startup') + '\Buddy on the PC.lnk')"
echo Done: Buddy no longer starts when you log in.
pause
