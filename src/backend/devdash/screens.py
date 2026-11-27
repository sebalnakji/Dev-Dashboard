"""화면분할 탭의 화면들 (~/.devdash/screens.json).

화면 하나에 세션 칸이 넷. 2분할이면 앞의 둘만 보이고 뒤의 둘은 그대로 남는다 (4→2→4 로 바꿔도
3 · 4번 칸이 돌아온다).

    {
      "version": 1,
      "screens": [{"id": "a1b2c3d4", "name": "업무", "layout": 2 | 4,
                   "cells": ["<목록 번호>" | null, null, null, null]}, ...]   목록 순서 = 화면 순서
    }

- 세션은 **목록 번호**(`claude agents` 의 id)로 둔다. 대화 번호는 /clear 마다 바뀌어 칸이 옛
  대화를 가리키게 된다
- 세션 배치(sessions.json)처럼 잠그고 읽어 고친 뒤 통째로 바꿔 끼운다 — 두 탭에서 동시에 고쳐도
  한쪽 갱신이 사라지거나 반쯤 쓴 파일을 읽지 않게
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from devdash.home import home

# isort: split
# 위의 devdash.home 이 플러그인 스크립트 폴더를 import 경로에 올린 뒤에야 불러올 수 있다
import devdash_store

VERSION = 1
CELLS = 4
LAYOUTS = (2, 4)
LOCK_WAIT = 5.0
NAME_MAX = 40


def path() -> Path:
    return home() / "screens.json"


def _screen(raw: object) -> dict | None:
    """저장된 화면 하나의 모양을 맞춘다. 알아볼 수 없으면 None."""
    if not isinstance(raw, dict):
        return None
    sid, name = raw.get("id"), raw.get("name")
    if not isinstance(sid, str) or not sid or not isinstance(name, str) or not name.strip():
        return None
    cells = raw.get("cells") if isinstance(raw.get("cells"), list) else []
    cells = [c if isinstance(c, str) and c else None for c in cells[:CELLS]]
    cells += [None] * (CELLS - len(cells))
    layout = raw.get("layout") if raw.get("layout") in LAYOUTS else 2
    return {"id": sid, "name": name.strip(), "layout": layout, "cells": cells}


def load(where: Path | None = None) -> dict:
    """읽어서 모양을 맞춘다. 없거나 깨졌으면 빈 목록."""
    try:
        data = json.loads((where or path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    screens = data.get("screens") if isinstance(data, dict) else None
    out = [s for s in map(_screen, screens if isinstance(screens, list) else []) if s]
    seen: set[str] = set()
    out = [s for s in out if not (s["id"] in seen or seen.add(s["id"]))]  # 같은 id 는 앞의 것만
    return {"version": VERSION, "screens": out}


def _save(data: dict, where: Path) -> None:
    """통째로 바꿔 끼운다. 읽는 쪽이 파일을 잡고 있으면 잠깐 실패하므로 몇 번 다시 한다."""
    tmp = where.with_name(f"{where.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        for attempt in range(10):
            try:
                os.replace(tmp, where)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def editing(where: Path | None = None) -> Iterator[dict]:
    """잠그고 읽어 고친 뒤 저장한다. 블록에서 예외가 나면 저장하지 않는다."""
    where = where or path()
    where.parent.mkdir(parents=True, exist_ok=True)
    busy = f"화면 설정을 다른 곳에서 고치고 있습니다: {where}"
    with devdash_store.locked(where, wait=LOCK_WAIT, busy=busy):
        data = load(where)
        yield data
        _save(data, where)


def _name(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("화면 이름을 적어 주세요")
    name = " ".join(name.split())
    if len(name) > NAME_MAX:
        raise ValueError(f"화면 이름은 {NAME_MAX}자까지입니다")
    return name


def find(data: dict, screen_id: str) -> dict:
    hit = next((s for s in data["screens"] if s["id"] == screen_id), None)
    if hit is None:
        raise KeyError("없는 화면입니다")
    return hit


def add(data: dict, name: object) -> dict:
    """끝에 새 화면 (2분할, 빈 칸 넷)."""
    screen = {"id": uuid.uuid4().hex[:8], "name": _name(name), "layout": 2, "cells": [None] * CELLS}
    data["screens"].append(screen)
    return screen


def rename(data: dict, screen_id: str, name: object) -> None:
    find(data, screen_id)["name"] = _name(name)


def set_layout(data: dict, screen_id: str, layout: object) -> None:
    """칸 설정은 넷 그대로 두고 보이는 칸 수만 바꾼다."""
    if layout not in LAYOUTS:
        raise ValueError("2분할 또는 4분할만 됩니다")
    find(data, screen_id)["layout"] = layout


def set_cell(data: dict, screen_id: str, index: int, session_id: str | None) -> None:
    """칸에 세션을 넣는다 (None 이면 비운다). 같은 화면의 다른 칸에 이미 있는 세션은 안 된다 —
    같은 대화가 두 칸에 보이면 칸만 낭비한다."""
    screen = find(data, screen_id)
    if not 0 <= index < CELLS:
        raise ValueError("없는 칸입니다")
    if session_id is not None:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("세션 목록 번호가 없습니다")
        if any(c == session_id for i, c in enumerate(screen["cells"]) if i != index):
            raise ValueError("이 화면의 다른 칸에 이미 있는 세션입니다")
    screen["cells"][index] = session_id


def remove(data: dict, screen_id: str) -> None:
    find(data, screen_id)
    data["screens"] = [s for s in data["screens"] if s["id"] != screen_id]


def reorder(data: dict, ids: list[str]) -> None:
    """ids 순서대로. 빠진 화면은 뒤에 그 순서대로 남는다 (다른 탭에서 막 만든 화면을 잃지 않게)."""
    rank = {sid: i for i, sid in enumerate(ids)}
    data["screens"].sort(key=lambda s: rank.get(s["id"], len(rank)))
