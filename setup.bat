@echo off
chcp 65001 >nul
rem 처음 설치. 가상환경(.venv)을 만들고 devdash 를 설치한 뒤, 나머지는 devdash setup 이 한다.
rem 다시 실행해도 안전하다. 코드를 새로 받은 뒤(git pull) 다시 돌리면 갱신된다.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install

call :find_python
if not defined PY (
  echo 파이썬 3.11 이상이 필요합니다. https://www.python.org 에서 설치한 뒤 다시 실행하세요.
  goto end
)
echo 가상환경^(.venv^)을 만듭니다
%PY% -m venv .venv || goto fail

:install
echo 백엔드를 설치합니다
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -e src\backend || goto fail
".venv\Scripts\python.exe" -m devdash setup
goto end

:fail
echo 설치에 실패했습니다. 위 오류를 확인하세요.

:end
echo.
pause
exit /b

:find_python
rem py 런처가 있으면 버전을 골라 쓰고, 없으면 PATH 의 python 을 쓴다.
rem 스토어 바로가기 python.exe 는 실행만 되고 아무것도 안 하는 경우가 있어 버전을 직접 확인한다
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=py -3" && exit /b
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
exit /b
