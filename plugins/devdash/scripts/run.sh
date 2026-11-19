#!/bin/sh
# 훅 · 스킬이 부르는 파이썬 실행기 — 훅 명령은 OS 마다 다르게 줄 수 없어서 여기서 고른다.
#   윈도우(Git Bash): py -3 · 그 밖(macOS · Linux): python3 · python 순으로 처음 찾은 것
# 사용: sh run.sh <이 폴더의 스크립트 이름> [인자 …]   (표준 입력은 그대로 넘어간다)
# 이 폴더: 윈도우 경로(\)로 불려도 되게 / 와 \ 둘 다로 자른다
dir=${0%[/\\]*}
[ "$dir" = "$0" ] && dir=.
script="$dir/$1"
shift
if command -v py >/dev/null 2>&1; then exec py -3 "$script" "$@"; fi
if command -v python3 >/dev/null 2>&1; then exec python3 "$script" "$@"; fi
exec python "$script" "$@"
