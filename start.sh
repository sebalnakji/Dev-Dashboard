#!/bin/sh
# 대시보드를 뒤에서 띄우고 브라우저를 연다. 이미 떠 있으면 브라우저만 연다 (devdash start)
ROOT=$(cd "$(dirname "$0")" && pwd)
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "아직 설치하지 않았습니다. ./setup.sh 를 먼저 실행하세요." >&2
  exit 1
fi
exec "$ROOT/.venv/bin/python" -m devdash start "$@"
