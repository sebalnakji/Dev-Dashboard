@echo off
chcp 65001 >nul
rem 대시보드를 뒤에서 띄우고 브라우저를 연다. 이미 떠 있으면 브라우저만 연다 (devdash start)
setlocal
set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
  echo 아직 설치하지 않았습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
"%PYEXE%" -m devdash start %*
if errorlevel 1 pause
