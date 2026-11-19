@echo off
cd /d "%~dp0"
echo Stopping douyinassis...
taskkill /F /IM douyinassis.exe
echo Stopped.
pause
