@echo off
rem Launcher. Real logic lives in scripts\start.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
