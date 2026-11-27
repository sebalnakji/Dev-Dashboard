"""Claude 세션 칸과 PC 메모리 칸의 API.

claude 명령 · tasklist 를 부르는 동기 코드라 async 가 아닌 def 로 둔다.
FastAPI 가 스레드에서 돌려 이벤트 루프(서버 수집 · SSE)를 막지 않는다.

세션은 목록 번호(`claude agents` 의 id)로 가리킨다.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from devdash import compat, records
from devdash.claude import agents, chat, registry
from devdash.claude.service import Result, SessionService
from devdash.local import ABILITIES, memory_status
from devdash.local.memsnap import MemoryWatcher

router = APIRouter(prefix="/api")

TOP_APPS = 10


def _service(request: Request) -> SessionService:
    return request.app.state.claude


def _answer(result: Result) -> JSONResponse:
    """서비스 결과를 응답으로. 실패는 {"error"}, 확인 요청은 {"confirm"} 를 409 로 보낸다."""
    if result is None:
        return JSONResponse({"ok": True})
    return JSONResponse(result if isinstance(result, dict) else {"error": result}, status_code=409)


def _with_row(
    request: Request, session_id: str, act: Callable[[SessionService, dict], Result]
) -> JSONResponse:
    service = _service(request)
    row = service.entry(session_id)
    if row is None:
        return JSONResponse({"error": "목록에 없는 세션입니다"}, status_code=404)
    return _answer(act(service, row))


class ForceBody(BaseModel):
    force: bool = False
    """백그라운드 작업이 함께 꺼진다 · 같은 이름이 있다는 확인을 받았으면 True."""


# --- 목록 · 대화 ---


@router.get("/claude/sessions")
def list_sessions(request: Request) -> dict:
    return _service(request).view()


@router.get("/claude/sessions/{key}/chat")
def session_chat(
    request: Request, key: str, limit: int = 10, project: str | None = None
) -> JSONResponse:
    """세션의 최근 질문 limit 개와 답. key 는 목록 번호(백그라운드) 또는 대화 번호(대화형).
    지금 목록에 보이는 세션만 — 아무 대화 기록이나 읽어 주는 통로가 되지 않게.
    project(프로젝트 키)가 오면 답마다 그 차례에 고친 파일(작업 상자)을 붙인다."""
    conversation = _service(request).conversation_of(key)
    if conversation is None:
        return JSONResponse({"error": "목록에 없는 세션입니다"}, status_code=404)
    path = chat.transcript(conversation)
    if path is None:
        return JSONResponse({"messages": [], "more": False})
    project = project if project and records.project(project) else None
    try:
        st = path.stat()
    except OSError:
        return JSONResponse({"messages": [], "more": False})
    stamp = (str(path), st.st_size, st.st_mtime_ns, limit, project)
    stamp += (records.work_stamp(project),) if project else ()
    with _chat_lock:
        hit = _chat_cache.get(stamp)
        if hit is not None:
            _chat_cache.move_to_end(stamp)
            return JSONResponse(hit)
    out = chat.chat(path, limit)
    if project:
        records.chat_work(project, out["messages"])
        out["shellTracked"] = records.shell_tracked(project)
    with _chat_lock:
        _chat_cache[stamp] = out
        while len(_chat_cache) > CHAT_CACHE_SIZE:
            _chat_cache.popitem(last=False)
    return JSONResponse(out)


CHAT_CACHE_SIZE = 32
"""기억해 두는 대화 응답 수. 화면분할 4칸 + 프로젝트 탭 + [더 보기] 로 늘린 것까지."""

_chat_cache: OrderedDict[tuple, dict] = OrderedDict()
"""(기록 파일 · 크기 · 수정 시각 · limit · 프로젝트 · 작업 상자 원본 상태) → 응답.
보는 화면이 5초마다 묻는데 대화는 그보다 드물게 바뀐다 — 그대로면 기록을 다시 읽지 않는다
(화면분할 4칸이 동시에 물으면 한 번에 1~2초 걸렸다)."""
_chat_lock = threading.Lock()


@router.get("/claude/compat")
def compat_status() -> dict:
    """지금 Claude Code 버전과 마지막으로 호환 시험을 통과한 버전. 다르면 세션 칸이 알린다."""
    return compat.status()


# --- 세션 동작 ---


@router.post("/claude/sessions/{session_id}/start")
def start_session(request: Request, session_id: str) -> JSONResponse:
    return _with_row(request, session_id, lambda s, row: s.start(row))


@router.post("/claude/sessions/{session_id}/stop")
def stop_session(request: Request, session_id: str, body: ForceBody | None = None) -> JSONResponse:
    force = bool(body and body.force)
    return _with_row(request, session_id, lambda s, row: s.stop(row, force=force))


@router.post("/claude/sessions/{session_id}/foreground")
def foreground_session(request: Request, session_id: str) -> JSONResponse:
    return _with_row(request, session_id, lambda s, row: s.foreground(row))


@router.post("/claude/sessions/{session_id}/delete")
def delete_session(
    request: Request, session_id: str, body: ForceBody | None = None
) -> JSONResponse:
    force = bool(body and body.force)
    return _with_row(request, session_id, lambda s, row: s.delete(row, force=force))


@router.post("/claude/deleted/{session_id}/restore")
def restore_session(request: Request, session_id: str) -> JSONResponse:
    return _answer(_service(request).restore(session_id))


# --- 새 세션 ---


class NewSession(BaseModel):
    cwd: str
    name: str
    group: str | None = None
    force: bool = False


@router.post("/claude/sessions")
def create_session(request: Request, body: NewSession) -> JSONResponse:
    return _answer(_service(request).create(body.cwd, body.name, body.group, force=body.force))


@router.get("/claude/folders")
def folder_choices() -> list[dict]:
    """새 세션의 폴더 후보: 프로젝트 탭에 등록된 폴더 + Claude 가 신뢰를 승인한 폴더.
    브라우저의 폴더 선택 창은 실제 경로를 주지 않아 목록에서 고르게 한다."""
    out: dict[str, dict] = {}
    for info in records.store.projects().values():
        root = info.get("root") or ""
        if os.path.isdir(root):
            out[records.store.same(root)] = {"path": root, "project": True}
    for path in agents.trusted_folders():
        out.setdefault(records.store.same(path), {"path": path, "project": False})
    return sorted(out.values(), key=lambda f: (not f["project"], f["path"].casefold()))


# --- 그룹 · 배치 ---


class GroupBody(BaseModel):
    name: str


class GroupPatch(BaseModel):
    name: str | None = None
    collapsed: bool | None = None
    index: int | None = None


class PlaceBody(BaseModel):
    group: str | None = None
    """None 이면 "분류 안 됨"."""
    order: list[str]
    """옮긴 뒤 그 그룹의 세션 순서 (목록 번호)."""


@router.post("/claude/groups")
def add_group(request: Request, body: GroupBody) -> JSONResponse:
    return _answer(_service(request).edit(lambda data: registry.add_group(data, body.name)))


@router.patch("/claude/groups/{name}")
def change_group(request: Request, name: str, body: GroupPatch) -> JSONResponse:
    def change(data: dict) -> None:
        current = name
        if body.name is not None:
            registry.rename_group(data, name, body.name)
            current = body.name.strip()
        if body.collapsed is not None:
            registry.set_collapsed(data, current, body.collapsed)
        if body.index is not None:
            registry.move_group(data, current, body.index)

    return _answer(_service(request).edit(change))


@router.delete("/claude/groups/{name}")
def delete_group(request: Request, name: str) -> JSONResponse:
    return _answer(_service(request).edit(lambda data: registry.remove_group(data, name)))


@router.put("/claude/sessions/{session_id}/place")
def place_session(request: Request, session_id: str, body: PlaceBody) -> JSONResponse:
    return _answer(_service(request).place(session_id, body.group, body.order))


# --- PC 메모리 ---


@router.get("/pc/abilities")
def pc_abilities() -> dict:
    """이 OS 에서 되는 것 (터미널 창 열기 · 폴더 선택 창). 화면이 안 되는 버튼을 숨긴다."""
    return ABILITIES


_apps = MemoryWatcher(TOP_APPS)


@router.get("/pc/memory")
def pc_memory() -> dict:
    """남은 메모리와 많이 쓰는 앱. 무엇을 끌지 고를 때 PC 전체 상황을 같이 보려고 둔다.
    앱별 메모리는 별도 프로세스가 뒤에서 10초마다 센 마지막 값이다 (memsnap) — 요청이 기다리지
    않고, 세는 동안 서버의 다른 요청도 밀리지 않는다. 처음 몇 초는 앱 목록이 빈다."""
    total, avail = memory_status()
    return {"total": total, "avail": avail, "apps": _apps.apps() or []}
