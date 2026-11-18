#!/bin/sh
# 처음 설치. 가상환경(.venv)을 만들고 devdash 를 설치한 뒤, 나머지는 devdash setup 이 한다.
# 다시 실행해도 안전하다. 코드를 새로 받은 뒤(git pull) 다시 돌리면 갱신된다.
set -e
ROOT=$(cd "$(dirname "$0")" && pwd)
PYEXE="$ROOT/.venv/bin/python"

if [ ! -x "$PYEXE" ]; then
  PY=""
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 &&
      "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY=$cand
      break
    fi
  done
  if [ -z "$PY" ]; then
    echo "파이썬 3.11 이상이 필요합니다." >&2
    exit 1
  fi
  echo "가상환경(.venv)을 만듭니다"
  if ! "$PY" -m venv "$ROOT/.venv"; then
    # 반쯤 만들어진 .venv 가 남으면 다음 실행이 이 단계를 건너뛴다
    rm -rf "$ROOT/.venv"
    echo "가상환경을 만들지 못했습니다. Debian · Ubuntu 라면 python3-venv 를 설치하세요:" >&2
    echo "  sudo apt install python3-venv" >&2
    exit 1
  fi
fi

echo "백엔드를 설치합니다"
"$PYEXE" -m pip install --quiet --upgrade pip
"$PYEXE" -m pip install --quiet -e "$ROOT/src/backend"
exec "$PYEXE" -m devdash setup
