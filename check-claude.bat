@echo off
chcp 65001 >nul
rem Claude Code 호환 시험 (devdash check-claude). 몇 분 걸린다. 결과를 볼 수 있게 끝나면 기다린다
setlocal
set "PYEXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYEXE%" (
  echo 아직 설치하지 않았습니다. setup.bat 을 먼저 실행하세요.
  pause
  exit /b 1
)
"%PYEXE%" -m devdash check-claude %*
pause
