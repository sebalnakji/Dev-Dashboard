#!/bin/sh
# Claude Code 호환 시험 (devdash check-claude). 몇 분 걸린다
ROOT=$(cd "$(dirname "$0")" && pwd)
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "아직 설치하지 않았습니다. ./setup.sh 를 먼저 실행하세요." >&2
  exit 1
fi
exec "$ROOT/.venv/bin/python" -m devdash check-claude "$@"
