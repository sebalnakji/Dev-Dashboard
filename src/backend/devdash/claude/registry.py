"""세션 칸의 배치 — 사람이 정한 것만 (~/.devdash/sessions.json).

세션 자체(이름 · 상태 · 폴더 · 대화 번호)는 매번 `claude agents --all` 에서 읽고 저장하지 않는다.
여기에는 그 목록에 없는, 사람이 정한 것만 둔다:

    {
      "version": 2,
      "groups":   [{"name": "LRS", "collapsed": false}, ...],     목록 순서 = 화면 순서
      "sessions": {"<목록 번호>": {"group": "LRS", "order": 0,
                                   "project": "<프로젝트 키>"}},   project 는 사람이 바꾼 때만
      "deleted":  [{"id", "name", "group", "cwd", "sessionId", "deletedAt"}, ...]
    }

- 세션은 **목록 번호**(`claude agents` 의 id)로 기억한다. /clear 해도 바뀌지 않는다. 대화 번호는
  /clear 마다 바뀌므로 키로 쓰면 그룹을 잃는다
- `group` 이 없거나 사라진 그룹이면 "분류 안 됨"
- `project` 가 없으면 작업 폴더로 자동. 빈 문자열이면 "어느 프로젝트에도 넣지 않음"
- `deleted` 는 [삭제]한 세션을 [복원]하기 위한 정보. KEEP_DELETED 가 지나면 지운다
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

from devdash.home import sessions_path

# isort: split
# 위의 devdash.home 이 플러그인 스크립트 폴더를 import 경로에 올린 뒤에야 불러올 수 있다
import devdash_store

VERSION = 2

LOCK_WAIT = 5.0
"""초. 다른 요청(화면의 두 탭 등)이 고치는 중이면 이만큼 기다린다."""

KEEP_DELETED = 30 * 24 * 3600
"""초. 삭제한 세션의 복원 정보를 두는 기간."""


def empty() -> dict:
    return {"version": VERSION, "groups": [], "sessions": {}, "deleted": []}


def load(path: Path | None = None) -> dict:
    """읽어서 모양을 맞춘다. 없거나 옛 형식(목록 배열)이면 빈 배치 — 옛 형식은 옮기지 않는다
    (옛 등록은 대화 번호를 키로 써서 그대로는 맞지 않는다)."""
    path = path or sessions_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty()
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return empty()
    out = empty()
    out["groups"] = [
        {"name": g["name"], "collapsed": bool(g.get("collapsed"))}
        for g in data.get("groups") or []
        if isinstance(g, dict) and isinstance(g.get("name"), str) and g["name"]
    ]
    sessions = data.get("sessions")
    if isinstance(sessions, dict):
        out["sessions"] = {k: v for k, v in sessions.items() if isinstance(v, dict)}
    out["deleted"] = [d for d in data.get("deleted") or [] if isinstance(d, dict) and d.get("id")]
    return out


def save(data: dict, path: Path | None = None) -> None:
    """통째로 바꿔 끼운다 — 쓰는 도중에 읽는 쪽(화면 폴링)이 반쯤 쓴 파일을 보지 않게.
    읽는 쪽이 파일을 잡고 있으면 바꿔 끼우기가 잠깐 실패하므로 몇 번 다시 한다.
    고칠 때는 이것을 직접 부르지 말고 editing() 을 쓴다."""
    path = path or sessions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
def editing(path: Path | None = None) -> Iterator[dict]:
    """배치를 고치는 길: 잠그고 읽어 고친 뒤 저장한다. 블록 안에서 받은 사전을 고친다.

    화면에서 끌어 놓기 · 그룹 바꾸기 · 삭제가 겹쳐 들어오면 한쪽 갱신이 사라질 수 있어 잠근다.
    블록에서 예외가 나면 저장하지 않는다. 저장할 때 기간이 지난 복원 정보를 지운다.
    """
    path = path or sessions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    busy = f"세션 배치를 다른 곳에서 고치고 있습니다: {path}"
    with devdash_store.locked(path, wait=LOCK_WAIT, busy=busy):  # OS 별 잠금은 한 곳에
        data = load(path)
        yield data
        prune_deleted(data)
        save(data, path)


def prune_deleted(data: dict, now: float | None = None) -> None:
    """복원 기간이 지난 삭제 기록을 지운다. 대화 파일은 건드리지 않는다 (Claude 가 정리한다)."""
    now = time.time() if now is None else now
    data["deleted"] = [
        d for d in data["deleted"] if now - float(d.get("deletedAt") or 0) < KEEP_DELETED
    ]


# --- 그룹 ---


def group_names(data: dict) -> list[str]:
    return [g["name"] for g in data["groups"]]


def add_group(data: dict, name: str) -> str:
    name = _group_name(name)
    if name in group_names(data):
        raise ValueError(f"이미 있는 그룹입니다: {name}")
    data["groups"].append({"name": name, "collapsed": False})
    return name


def rename_group(data: dict, old: str, new: str) -> None:
    new = _group_name(new)
    group = _find_group(data, old)
    if new != old and new in group_names(data):
        raise ValueError(f"이미 있는 그룹입니다: {new}")
    group["name"] = new
    for s in data["sessions"].values():
        if s.get("group") == old:
            s["group"] = new


def remove_group(data: dict, name: str) -> None:
    """그룹을 지운다. 안의 세션은 "분류 안 됨"으로 간다 (세션은 지우지 않는다)."""
    data["groups"].remove(_find_group(data, name))
    for s in data["sessions"].values():
        if s.get("group") == name:
            s.pop("group", None)


def set_collapsed(data: dict, name: str, collapsed: bool) -> None:
    _find_group(data, name)["collapsed"] = collapsed


def move_group(data: dict, name: str, index: int) -> None:
    group = _find_group(data, name)
    data["groups"].remove(group)
    data["groups"].insert(max(0, min(index, len(data["groups"]))), group)


def _find_group(data: dict, name: str) -> dict:
    for g in data["groups"]:
        if g["name"] == name:
            return g
    raise ValueError(f"없는 그룹입니다: {name}")


def _group_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("그룹 이름이 비었습니다")
    return name


# --- 세션 ---


def group_of(data: dict, session_id: str) -> str | None:
    """세션의 그룹. 지정이 없거나 그 그룹이 사라졌으면 None ("분류 안 됨")."""
    group = data["sessions"].get(session_id, {}).get("group")
    return group if group in group_names(data) else None


def order_of(data: dict, session_id: str) -> int | None:
    order = data["sessions"].get(session_id, {}).get("order")
    return order if isinstance(order, int) else None


def place(data: dict, session_id: str, group: str | None, ordered_ids: list[str]) -> None:
    """세션을 group 으로 옮기고, 그 그룹의 순서를 ordered_ids 대로 다시 매긴다.

    ordered_ids 는 화면이 보낸 "옮긴 뒤 그 그룹의 세션 순서". 옮긴 세션이 빠져 있으면 맨 뒤에
    둔다. group 이 None 이면 "분류 안 됨"."""
    if group is not None and group not in group_names(data):
        raise ValueError(f"없는 그룹입니다: {group}")
    if session_id not in ordered_ids:
        ordered_ids = [*ordered_ids, session_id]
    for order, sid in enumerate(ordered_ids):
        entry = data["sessions"].setdefault(sid, {})
        entry["order"] = order
        if group is None:
            entry.pop("group", None)
        else:
            entry["group"] = group


def project_of(data: dict, session_id: str) -> str | None:
    """사람이 정한 프로젝트 키. None 이면 정하지 않음(자동), "" 이면 어느 프로젝트에도 넣지 않음."""
    project = data["sessions"].get(session_id, {}).get("project")
    return project if isinstance(project, str) else None


def set_project(data: dict, session_id: str, project: str | None) -> None:
    """프로젝트 소속을 사람이 정한다. None 이면 다시 자동(작업 폴더)으로."""
    entry = data["sessions"].setdefault(session_id, {})
    if project is None:
        entry.pop("project", None)
    else:
        entry["project"] = project


def forget(data: dict, session_id: str) -> None:
    data["sessions"].pop(session_id, None)
