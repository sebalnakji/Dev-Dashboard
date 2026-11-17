@echo off
rem First-time install. Real logic lives in scripts\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
echo.
pause
