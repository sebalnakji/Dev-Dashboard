"""관리할 Claude 세션 목록 (~/.devdash/sessions.json).

세션마다 대화 번호(id) · 이름 · 그룹 · 프로젝트 폴더를 둔다. 프로젝트 탭에 넣은 세션에는
project_key(devdash 에 등록된 프로젝트의 키)도 둔다 — 사람이 [세션 고르기]로 정한다 (ADR 0007).
Claude Code 는 재부팅하면 세션 이름을 잊고 번호로 되돌리지만,
이 목록을 근거로 항상 제대로 된 이름·그룹·프로젝트를 보여준다.
서버 목록(servers.yaml)처럼 이 파일은 커밋하지 않고 예시만 커밋한다.
"""

from __future__ import annotations

import contextlib
import json
import msvcrt
import os
import time
from collections.abc import Iterator
from pathlib import Path

from devdash.home import sessions_path

LOCK_WAIT = 5.0
"""초. 다른 쪽(/clear 훅 · 화면 · 명령)이 고치는 중이면 이만큼 기다린다."""


def norm(path: str) -> str:
    """경로 표기를 맞춘다. 백슬래시·끝 슬래시 차이로 같은 폴더가 달라 보이지 않게."""
    return (path or "").replace("\\", "/").rstrip("/")


def load(path: Path | None = None) -> list[dict]:
    path = path or sessions_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("sessions", [])


def save(entries: list[dict], path: Path | None = None) -> None:
    """통째로 바꿔 끼운다 — 쓰는 도중에 읽는 쪽(화면 폴링)이 반쯤 쓴 파일을 보지 않게.
    읽는 쪽이 파일을 잡고 있으면 바꿔 끼우기가 잠깐 실패하므로 몇 번 다시 한다.
    고칠 때는 이것을 직접 부르지 말고 editing() 을 쓴다."""
    path = path or sessions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"sessions": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def editing(path: Path | None = None) -> Iterator[list[dict]]:
    """목록을 고치는 길: 잠그고 읽어 고친 뒤 저장한다. 블록 안에서 받은 목록을 고친다.

    /clear 훅 · 화면의 [세션 고르기] · claude add/remove 가 동시에 고치면 한쪽 갱신이
    사라졌다 (6단계 Fable 재현: /clear 로 바뀐 번호가 옛 번호로 되돌아감).
    블록에서 예외가 나면 저장하지 않는다.
    """
    path = path or sessions_path()
    lock = path.with_name(path.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"세션 목록을 다른 곳에서 고치고 있습니다: {path}") from None
                time.sleep(0.02)
        try:
            entries = load(path)
            yield entries
            save(entries, path)
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def find(entries: list[dict], key: str) -> list[dict]:
    """번호 앞자리 또는 이름으로 찾는다."""
    return [e for e in entries if e["id"].startswith(key) or e.get("name") == key]


def by_group(entries: list[dict]) -> list[tuple[str, list[dict]]]:
    """그룹은 가나다순, 그룹 안 세션은 이름순 (Claude 앱 사이드바와 같은 정렬)."""
    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(e.get("group") or "(그룹없음)", []).append(e)
    return [
        (g, sorted(groups[g], key=lambda e: e["name"].casefold()))
        for g in sorted(groups, key=str.casefold)
    ]
