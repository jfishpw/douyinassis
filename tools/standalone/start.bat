@echo off
cd /d "%~dp0"
echo Starting douyinassis...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
echo.
echo If the browser and panel opened, you can close this window.
pause
