"""이 폴더의 코드 상태 — start 가 '떠 있는 서버 · 화면 빌드본이 지금 코드와 같은가' 를
견주는 데 쓴다.

코드를 합친(git pull · merge) 뒤 아이콘(start)만 눌러도 반영되게:
- 서버는 띄워질 때의 코드 상태를 기억해 /api/health 로 알린다.
  지금 코드와 다르면 start 가 다시 띄운다
- 화면 빌드본 옆에 빌드한 커밋을 적어 둔다.
  그 뒤 화면 코드(src/frontend)가 바뀌었으면 start 가 빌드한다

git 이 없거나 git 저장소가 아니면(압축 파일로 받음) 견줄 수 없어 지금처럼 동작한다.
"""

from __future__ import annotations

import contextlib
import json
import subprocess

from devdash.home import REPO

FRONTEND = "src/frontend"
BUILD_STAMP = REPO / FRONTEND / "dist" / ".devdash-build.json"
"""화면 빌드본을 만든 코드 상태. dist 는 git 에 올리지 않으므로 이 파일도 올라가지 않는다."""


def _git(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def current() -> dict:
    """{"commit": 지금 커밋 | None, "clean": 고친 채 커밋 안 한 파일이 없나 | None}.
    새로 만든(추적하지 않는) 파일은 세지 않는다 — 코드가 아니라 메모 · 임시 파일인 경우가 많다."""
    head = _git("rev-parse", "HEAD")
    if head is None or head.returncode != 0:
        return {"commit": None, "clean": None}
    status = _git("status", "--porcelain", "--untracked-files=no")
    clean = status is not None and status.returncode == 0 and not status.stdout.strip()
    return {"commit": head.stdout.strip(), "clean": clean}


def same(running: object, now: dict) -> bool:
    """떠 있는 서버(running = 그 서버의 /api/health 'code')가 지금 코드와 같은가.
    - git 으로 견줄 수 없으면 같다고 본다 (지금처럼 브라우저만 연다)
    - 이 기능 전의 서버(code 를 대지 않음)는 다르다고 본다
    - 어느 쪽이든 고친 채 커밋 안 한 파일이 있으면 같은지 알 수 없어 다르다고 본다"""
    if not now.get("commit"):
        return True
    if not isinstance(running, dict):
        return False
    return (
        running.get("commit") == now["commit"]
        and running.get("clean") is True
        and now.get("clean") is True
    )


def frontend_build_needed(now: dict | None = None) -> tuple[bool, bool]:
    """(화면을 빌드해야 하나, 패키지부터 다시 받아야 하나).

    빌드본이 없거나 · 빌드한 커밋을 모르거나(이 기능 전 빌드) · 그 뒤 src/frontend 가 바뀌었거나 ·
    지금 src/frontend 에 커밋 안 한 변경이 있으면 빌드한다. package.json · package-lock.json 이
    바뀌었으면 패키지부터 다시 받는다 (npm ci)."""
    now = now or current()
    if not (REPO / FRONTEND / "dist" / "index.html").exists():
        return True, False
    if not now.get("commit"):
        return False, False  # git 으로 견줄 수 없다 — 있는 빌드본을 쓴다
    try:
        built = json.loads(BUILD_STAMP.read_text(encoding="utf-8")).get("commit")
    except (OSError, ValueError, AttributeError):
        built = None
    local = _git("status", "--porcelain", "--untracked-files=no", "--", FRONTEND)
    dirty = local is not None and bool(local.stdout.strip())
    if not built:
        return True, False
    if built == now["commit"]:
        return dirty, False
    diff = _git("diff", "--name-only", built, now["commit"], "--", FRONTEND)
    if diff is None or diff.returncode != 0:
        return True, False  # 빌드한 커밋을 못 찾는다 (기록이 바뀜) — 빌드해 둔다
    names = [n for n in diff.stdout.splitlines() if n.strip()]
    deps = any(n.endswith(("package.json", "package-lock.json")) for n in names)
    return bool(names) or dirty, deps


def write_build_stamp(now: dict | None = None) -> None:
    """화면을 빌드한 뒤 그 코드 상태를 적는다."""
    now = now or current()
    # 못 적으면 다음 start 가 한 번 더 빌드할 뿐이다
    with contextlib.suppress(OSError):
        BUILD_STAMP.write_text(json.dumps(now), encoding="utf-8")
