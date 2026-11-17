"""Claude 세션 칸과 PC 메모리 칸의 API (ADR 0005).

claude 명령·tasklist 를 부르는 동기 코드라 async 가 아닌 def 로 둔다.
FastAPI 가 스레드에서 돌려 이벤트 루프(서버 수집·SSE)를 막지 않는다.
"""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from devdash.claude import chat
from devdash.claude.service import Result, SessionService
from devdash.local import app_memory, memory_status

router = APIRouter(prefix="/api")

TOP_APPS = 10
MEMORY_CACHE_SECONDS = 5
"""tasklist 를 읽는 데 ~0.5초 걸려, 이 안의 요청은 같은 결과를 돌려준다."""


def _service(request: Request) -> SessionService:
    return request.app.state.claude


def _answer(result: Result) -> JSONResponse:
    """서비스 결과를 응답으로. 실패는 {"error"}, 확인 요청은 {"confirm"} 를 409 로 보낸다."""
    if result is None:
        return JSONResponse({"ok": True})
    return JSONResponse(result if isinstance(result, dict) else {"error": result}, status_code=409)


def _entry_or_404(service: SessionService, session_id: str) -> dict | JSONResponse:
    entry = service.entry(session_id)
    if entry is None:
        return JSONResponse({"error": "목록에 없는 세션입니다"}, status_code=404)
    return entry


@router.get("/claude/sessions")
def list_sessions(request: Request) -> list[dict]:
    return _service(request).rows()


@router.post("/claude/sessions/{session_id}/start")
def start_session(request: Request, session_id: str) -> JSONResponse:
    service = _service(request)
    entry = _entry_or_404(service, session_id)
    return entry if isinstance(entry, JSONResponse) else _answer(service.start(entry))


class StopBody(BaseModel):
    force: bool = False
    """백그라운드 작업이 함께 꺼진다는 확인을 받았으면 True."""


@router.post("/claude/sessions/{session_id}/stop")
def stop_session(request: Request, session_id: str, body: StopBody | None = None) -> JSONResponse:
    service = _service(request)
    entry = _entry_or_404(service, session_id)
    if isinstance(entry, JSONResponse):
        return entry
    return _answer(service.stop(entry, force=bool(body and body.force)))


@router.post("/claude/sessions/{session_id}/foreground")
def foreground_session(request: Request, session_id: str) -> JSONResponse:
    service = _service(request)
    entry = _entry_or_404(service, session_id)
    return entry if isinstance(entry, JSONResponse) else _answer(service.foreground(entry))


@router.get("/claude/sessions/{session_id}/chat")
def session_chat(request: Request, session_id: str, limit: int = 10) -> JSONResponse:
    """세션의 최근 질문 limit 개와 답 (프로젝트 탭 [세션] 보기, T-8).
    세션 목록(sessions.json)에 있는 세션만 — 아무 대화 기록이나 읽어 주는 통로가 되지 않게."""
    entry = _entry_or_404(_service(request), session_id)
    if isinstance(entry, JSONResponse):
        return entry
    path = chat.transcript(session_id)
    if path is None:
        return JSONResponse({"messages": [], "more": False})
    return JSONResponse(chat.chat(path, limit))


@router.post("/claude/start-all")
def start_all(request: Request) -> JSONResponse:
    return _answer(_service(request).start_all())


_memory: tuple[float, dict] = (0.0, {})
_memory_lock = threading.Lock()


@router.get("/pc/memory")
def pc_memory() -> dict:
    """남은 메모리와 많이 쓰는 앱. 무엇을 끌지 고를 때 PC 전체 상황을 같이 보려고 둔다."""
    global _memory
    with _memory_lock:
        if time.time() - _memory[0] > MEMORY_CACHE_SECONDS:
            total, avail = memory_status()
            ranked = sorted(app_memory().items(), key=lambda kv: kv[1], reverse=True)
            _memory = (
                time.time(),
                {
                    "total": total,
                    "avail": avail,
                    "apps": [{"name": n, "bytes": b} for n, b in ranked[:TOP_APPS]],
                },
            )
        return _memory[1]
