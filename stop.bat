@echo off
rem Launcher. Real logic lives in scripts\stop.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"
