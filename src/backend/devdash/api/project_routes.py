"""프로젝트 탭의 API (ADR 0007 · 0008).

칸마다 요청 하나다. 파일 · git 을 읽는 동기 코드라 async 가 아닌 def 로 두어 FastAPI 가
스레드에서 돌리게 한다 (서버 수집 · SSE 를 막지 않게).

실패는 {"error": 이유} 로 준다. 404 는 등록 안 된 프로젝트 · 없는 할 일,
409 는 지금 할 수 없는 일(폴더 없음 · 이미 등록 · 목록이 바뀜 · 창이 이미 열림)이다.
"""

from __future__ import annotations

import os
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from devdash import local, records
from devdash.claude import registry
from devdash.commit_summary import SummaryError

router = APIRouter(prefix="/api/projects")


def _rows(request: Request) -> list[dict]:
    """세션 칸과 같은 줄 (목록 번호 · 이름 · 작업 폴더 · 상태 · 사람이 정한 프로젝트)."""
    return request.app.state.claude.rows()


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _check(key: str, need_folder: bool = False) -> JSONResponse | None:
    """등록 안 된 프로젝트면 404, 폴더가 사라졌으면 409. 문제없으면 None.
    폴더가 사라진 것을 'git 이 아닌 빈 폴더'로 보이지 않게 따로 알린다."""
    info = records.project(key)
    if info is None:
        return _error("등록되지 않은 프로젝트입니다", 404)
    if need_folder and not os.path.isdir(info["root"]):
        return _error(f"프로젝트 폴더가 없습니다: {info['root']}", 409)
    return None


# --- 목록 · 추가 ---


@router.get("")
def list_projects() -> list[dict]:
    return records.list_projects()


@router.get("/candidates")
def candidates(request: Request) -> list[dict]:
    return records.candidates(_rows(request))


class AddBody(BaseModel):
    root: str
    name: str | None = None


@router.post("")
def add_project(body: AddBody) -> JSONResponse:
    try:
        return JSONResponse(records.add_project(body.root, body.name))
    except ValueError as exc:
        return _error(str(exc), 409)


class OrderBody(BaseModel):
    keys: list[str]


@router.put("/order")
def set_order(body: OrderBody) -> JSONResponse:
    """프로젝트 이름 상자를 끌어 바꾼 순서."""
    records.set_order(body.keys)
    return JSONResponse({"ok": True})


class RenameBody(BaseModel):
    name: str


@router.patch("/{key}")
def rename(key: str, body: RenameBody) -> JSONResponse:
    if bad := _check(key):
        return bad
    try:
        return JSONResponse(records.rename(key, body.name))
    except ValueError as exc:
        return _error(str(exc), 409)


@router.delete("/{key}")
def unregister(request: Request, key: str) -> JSONResponse:
    """등록 해제. 기록은 남는다 (같은 폴더를 다시 추가하면 이어진다)."""
    if bad := _check(key):
        return bad
    records.unregister(key)
    try:
        with registry.editing(request.app.state.claude.path) as data:
            records.drop_members(key, data)
    except TimeoutError:
        pass  # 세션 목록을 못 고쳐도 등록 해제는 끝났다. 세션은 다음에 고를 때 다른 데로 옮겨진다
    return JSONResponse({"ok": True})


_picking = threading.Lock()


@router.post("/pick-folder")
def pick_folder() -> JSONResponse:
    """이 PC 에 폴더 선택 창을 띄운다. 고르면 경로, 취소하면 null.
    연달아 누르면 창이 여러 개 뜨므로 열려 있는 동안은 거절한다."""
    if not _picking.acquire(blocking=False):
        return _error("폴더 선택 창이 이미 열려 있습니다", 409)
    try:
        return JSONResponse({"path": local.pick_folder()})
    except OSError as exc:
        return _error(f"폴더 선택 창을 띄우지 못했습니다: {exc}", 409)
    finally:
        _picking.release()


# --- 한 프로젝트의 칸들 ---


@router.get("/{key}/files")
def files(key: str) -> JSONResponse:
    return _check(key, need_folder=True) or JSONResponse(records.files(key))


@router.get("/{key}/file-history")
def file_history(key: str, path: str) -> JSONResponse:
    """path: 저장소 기준 경로 (파일 목록의 path 그대로)."""
    return _check(key) or JSONResponse(records.file_history(key, path))


@router.get("/{key}/file")
def read_file(key: str, path: str) -> JSONResponse:
    """[파일] 보기에서 고른 문서의 내용. 폴더 밖 · 큰 파일 · 글이 아닌 파일은 409, 없으면 404."""
    if bad := _check(key, need_folder=True):
        return bad
    try:
        return JSONResponse(records.read_file(key, path))
    except FileNotFoundError as exc:
        return _error(str(exc), 404)
    except ValueError as exc:
        return _error(str(exc), 409)


IMAGE_HEADERS = {
    # 받은 형식 그대로만 해석하게 (글을 그림이라고 속여 HTML 로 돌리지 못하게)
    "X-Content-Type-Options": "nosniff",
    # 화면은 <img> 로만 그려 svg 안의 스크립트가 돌지 않는다. 이 주소를 직접 열어도 돌지 않게 막는다
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "Cache-Control": "no-cache",
}


@router.get("/{key}/image")
def read_image(key: str, path: str) -> Response:
    """[파일] 보기 · 문서 속 그림. 폴더 밖 · 큰 파일 · 그림이 아닌 파일은 409, 없으면 404."""
    if bad := _check(key, need_folder=True):
        return bad
    try:
        content, kind = records.read_image(key, path)
    except FileNotFoundError as exc:
        return _error(str(exc), 404)
    except ValueError as exc:
        return _error(str(exc), 409)
    return Response(content, media_type=kind, headers=IMAGE_HEADERS)


@router.get("/{key}/sessions")
def sessions(request: Request, key: str) -> JSONResponse:
    if bad := _check(key):
        return bad
    return JSONResponse(records.sessions(key, _rows(request)))


@router.get("/{key}/session-choices")
def session_choices(request: Request, key: str) -> JSONResponse:
    return _check(key) or JSONResponse(records.session_choices(key, _rows(request)))


class MembersBody(BaseModel):
    ids: list[str]


@router.put("/{key}/sessions")
def set_sessions(request: Request, key: str, body: MembersBody) -> JSONResponse:
    if bad := _check(key):
        return bad
    try:
        # 세션 칸의 끌어 놓기 · 삭제와 겹쳐도 갱신이 사라지지 않게 잠근 채 고친다
        rows = _rows(request)
        with registry.editing(request.app.state.claude.path) as data:
            records.set_members(key, data, rows, body.ids)
    except (ValueError, TimeoutError) as exc:
        return _error(str(exc), 409)
    return JSONResponse({"ok": True})


@router.get("/{key}/tasks")
def task_list(key: str) -> JSONResponse:
    return _check(key) or JSONResponse(records.task_list(key))


@router.get("/{key}/tasks/{number}")
def task_detail(key: str, number: int, file: str | None = None) -> JSONResponse:
    """file: 같은 번호가 둘일 때 화면이 고른 파일 이름."""
    if bad := _check(key):
        return bad
    detail = records.task_detail(key, number, file)
    if detail is None:
        return _error(f"T-{number} 이 없거나 읽을 수 없습니다", 404)
    return JSONResponse(detail)


@router.get("/{key}/tasks/{number}/diff")
def task_diff(key: str, number: int, path: str, full: bool = False) -> JSONResponse:
    """작업 문서의 바뀐 파일을 누르면: 이 작업이 그 파일을 어떻게 바꿨나 (T-19)."""
    return _check(key, need_folder=True) or JSONResponse(records.task_diff(key, number, path, full))


@router.get("/{key}/git")
def git_history(key: str) -> JSONResponse:
    return _check(key, need_folder=True) or JSONResponse(records.git_history(key))


@router.post("/{key}/git/fetch")
def git_fetch(key: str) -> JSONResponse:
    """[갱신] 버튼: 원격의 새 커밋을 지금 받아 온다.
    실패해도 200 에 이유를 담는다 (화면이 그대로 띄운다)."""
    return _check(key, need_folder=True) or JSONResponse(records.fetch(key))


@router.get("/{key}/commits/{sha}")
def commit_detail(key: str, sha: str) -> JSONResponse:
    if bad := _check(key, need_folder=True):
        return bad
    detail = records.commit_detail(key, sha)
    return JSONResponse(detail) if detail else _error("그 커밋을 찾지 못했습니다", 404)


@router.get("/{key}/commits/{sha}/diff")
def commit_diff(
    key: str, sha: str, path: str, old: str | None = None, full: bool = False
) -> JSONResponse:
    if bad := _check(key, need_folder=True):
        return bad
    diff = records.commit_diff(key, sha, path, old, full)
    return JSONResponse(diff) if diff else _error("그 커밋을 찾지 못했습니다", 404)


@router.get("/{key}/commits/{sha}/summary")
def commit_summary(key: str, sha: str) -> JSONResponse:
    """저장된 AI 요약. 아직 없으면 text 가 null (화면이 POST 로 만들기를 부탁한다)."""
    if bad := _check(key, need_folder=True):
        return bad
    found = records.commit_summary(key, sha)
    return JSONResponse(found) if found else _error("그 커밋을 찾지 못했습니다", 404)


@router.post("/{key}/commits/{sha}/summary")
def make_commit_summary(key: str, sha: str) -> JSONResponse:
    """AI 요약을 만든다 (Sonnet, 수십 초). 이미 있으면 그것을 준다. 만들지 못하면 409 에 이유."""
    if bad := _check(key, need_folder=True):
        return bad
    try:
        made = records.commit_summary(key, sha, create=True)
    except SummaryError as exc:
        return _error(str(exc), 409)
    return JSONResponse(made) if made else _error("그 커밋을 찾지 못했습니다", 404)
