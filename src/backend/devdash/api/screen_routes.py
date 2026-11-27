"""화면분할 탭의 API — 화면 목록 · 추가 · 이름 · 분할 · 칸 · 지우기 · 순서, 칸에 쓸 세션 줄.

실패는 {"error"} 를 409 (없는 화면은 404) 로 보낸다. 고친 뒤에는 화면 목록 전체를 돌려준다 —
화면이 받은 대로 바로 그리게.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from devdash import records, screens

router = APIRouter(prefix="/api/screens")


def _change(change: Callable[[dict], object]) -> JSONResponse:
    try:
        with screens.editing() as data:
            made = change(data)
    except KeyError as exc:
        return JSONResponse({"error": exc.args[0]}, status_code=404)
    except (ValueError, TimeoutError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    body = {"screens": data["screens"]}
    if isinstance(made, dict):
        body["made"] = made
    return JSONResponse(body)


@router.get("")
def list_screens() -> dict:
    return {"screens": screens.load()["screens"]}


class NameBody(BaseModel):
    name: str


@router.post("")
def add_screen(body: NameBody) -> JSONResponse:
    """끝에 새 화면. 응답의 made 가 만든 화면."""
    return _change(lambda d: screens.add(d, body.name))


class PatchBody(BaseModel):
    name: str | None = None
    layout: int | None = None


@router.patch("/{screen_id}")
def patch_screen(screen_id: str, body: PatchBody) -> JSONResponse:
    """이름 · 분할(2 · 4). 칸 설정은 분할을 바꿔도 그대로다."""

    def change(d: dict) -> None:
        if body.name is not None:
            screens.rename(d, screen_id, body.name)
        if body.layout is not None:
            screens.set_layout(d, screen_id, body.layout)

    return _change(change)


class CellBody(BaseModel):
    session: str | None = None
    """세션의 목록 번호. 없으면 칸을 비운다."""


@router.put("/{screen_id}/cells/{index}")
def set_cell(screen_id: str, index: int, body: CellBody) -> JSONResponse:
    return _change(lambda d: screens.set_cell(d, screen_id, index, body.session))


@router.delete("/{screen_id}")
def remove_screen(screen_id: str) -> JSONResponse:
    return _change(lambda d: screens.remove(d, screen_id))


class OrderBody(BaseModel):
    ids: list[str]


@router.put("/order")
def reorder(body: OrderBody) -> JSONResponse:
    return _change(lambda d: screens.reorder(d, body.ids))


@router.get("/sessions")
def session_rows(request: Request) -> list[dict]:
    """칸 머리줄 · [세션 고르기] 에 쓸 백그라운드 세션 줄 — 세션 칸과 같은 값(이름 · 그룹 · 켜짐 ·
    하는 일 · 토큰)에, 파일 · 할 일 링크가 갈 프로젝트(project, 없으면 null)를 더한다.
    터미널에서 연 세션은 목록 번호가 없어 칸에 넣을 수 없으므로 빠진다."""
    rows = request.app.state.claude.rows()
    homes = records.session_projects(rows)
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "group": r.get("group"),
            "order": r.get("order"),
            "state": r.get("state", "off"),
            "activity": r.get("activity"),
            "tokens": r.get("tokens"),
            "compactions": r.get("compactions", 0),
            "project": homes.get(r["id"]),
        }
        for r in rows
    ]
