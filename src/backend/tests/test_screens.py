"""화면분할 탭: 화면 저장(screens.json) · API · 칸의 세션 줄 · 대화 응답 기억."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devdash import records, screens
from devdash.api import claude_routes
from devdash.api.claude_routes import router as claude_router
from devdash.api.screen_routes import router as screen_router
from devdash.claude import chat


class FakeSessions:
    """칸 API 가 쓰는 것: rows() (세션 칸과 같은 줄) · conversation_of (대화 API)."""

    def __init__(self, rows: list[dict]):
        self.base = rows

    def rows(self) -> list[dict]:
        return self.base

    def conversation_of(self, key: str) -> str | None:
        return next((r["conversation"] for r in self.base if r["id"] == key), None)


def row(sid, name, cwd, **kw) -> dict:
    return {
        "id": sid,
        "name": name,
        "group": "업무",
        "order": 0,
        "cwd": str(cwd),
        "state": "on",
        "activity": "working",
        "tokens": 1200,
        "compactions": 1,
        "conversation": f"{sid}-conv",
        **kw,
    }


@pytest.fixture
def app(tmp_path):
    app = FastAPI()
    app.state.claude = FakeSessions([])
    app.include_router(screen_router)
    app.include_router(claude_router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# --- 저장 ---


def test_없거나_깨진_파일은_빈_목록이고_모양이_틀린_화면은_버린다():
    assert screens.load() == {"version": 1, "screens": []}
    screens.path().parent.mkdir(parents=True, exist_ok=True)
    screens.path().write_text("{깨짐", encoding="utf-8")
    assert screens.load()["screens"] == []
    raw = {
        "screens": [
            {"id": "a", "name": "업무", "layout": 4, "cells": ["s1", None]},
            {"id": "a", "name": "같은 id", "cells": []},
            {"id": "b", "name": "  ", "cells": []},
            {"id": "c", "name": "옛", "layout": 3, "cells": ["s1", 5, "", "s4", "s5"]},
            "문자열",
        ]
    }
    screens.path().write_text(json.dumps(raw), encoding="utf-8")
    assert screens.load()["screens"] == [
        {"id": "a", "name": "업무", "layout": 4, "cells": ["s1", None, None, None]},
        {"id": "c", "name": "옛", "layout": 2, "cells": ["s1", None, None, "s4"]},
    ]


def test_분할을_바꿔도_네_칸은_그대로다():
    with screens.editing() as d:
        sid = screens.add(d, "업무")["id"]
        screens.set_layout(d, sid, 4)
        for i, s in enumerate(["s1", "s2", "s3", "s4"]):
            screens.set_cell(d, sid, i, s)
    with screens.editing() as d:
        screens.set_layout(d, sid, 2)
    assert screens.load()["screens"][0]["cells"] == ["s1", "s2", "s3", "s4"]
    with screens.editing() as d:
        screens.set_layout(d, sid, 4)
    assert screens.load()["screens"][0] == {
        "id": sid,
        "name": "업무",
        "layout": 4,
        "cells": ["s1", "s2", "s3", "s4"],
    }


def test_블록에서_예외가_나면_저장하지_않는다():
    with screens.editing() as d:
        screens.add(d, "업무")
    with pytest.raises(ValueError), screens.editing() as d:
        screens.add(d, "둘째")
        screens.add(d, " ")
    assert [s["name"] for s in screens.load()["screens"]] == ["업무"]


def test_동시에_고쳐도_한쪽이_사라지지_않는다():
    """두 탭이 한꺼번에 화면을 더해도 모두 남는다 (잠그고 읽어 고친 뒤 바꿔 끼운다)."""

    def add(i):
        with screens.editing() as d:
            screens.add(d, f"화면{i}")

    threads = [threading.Thread(target=add, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(s["name"] for s in screens.load()["screens"]) == [f"화면{i}" for i in range(8)]
    assert not list(Path(screens.path()).parent.glob("screens.json.*.tmp"))


# --- API ---


def test_화면을_만들고_이름과_분할과_칸을_바꾸고_지운다(client):
    r = client.post("/api/screens", json={"name": " 업무  화면 "})
    assert r.status_code == 200
    made = r.json()["made"]
    assert made["name"] == "업무 화면" and made["layout"] == 2 and made["cells"] == [None] * 4
    sid = made["id"]
    assert (
        client.patch(f"/api/screens/{sid}", json={"name": "개인", "layout": 4}).status_code == 200
    )
    body = client.put(f"/api/screens/{sid}/cells/3", json={"session": "aaaaaaaa"}).json()
    assert body["screens"][0] == {
        "id": sid,
        "name": "개인",
        "layout": 4,
        "cells": [None, None, None, "aaaaaaaa"],
    }
    # 세션은 목록 번호로 저장된다
    assert json.loads(screens.path().read_text("utf-8"))["screens"][0]["cells"][3] == "aaaaaaaa"
    client.put(f"/api/screens/{sid}/cells/3", json={"session": None})
    assert client.get("/api/screens").json()["screens"][0]["cells"] == [None] * 4
    assert client.delete(f"/api/screens/{sid}").json() == {"screens": []}


@pytest.mark.parametrize(
    ("method", "path", "body", "status", "reason"),
    [
        ("patch", "/api/screens/없음", {"name": "x"}, 404, "없는 화면"),
        ("patch", "/api/screens/{sid}", {"layout": 3}, 409, "2분할 또는 4분할"),
        ("patch", "/api/screens/{sid}", {"name": ""}, 409, "이름을 적어"),
        ("put", "/api/screens/{sid}/cells/4", {"session": "b"}, 409, "없는 칸"),
        ("put", "/api/screens/{sid}/cells/1", {"session": "a"}, 409, "다른 칸에 이미"),
        ("delete", "/api/screens/없음", None, 404, "없는 화면"),
    ],
)
def test_잘못된_요청은_이유를_돌려주고_저장하지_않는다(client, method, path, body, status, reason):
    sid = client.post("/api/screens", json={"name": "업무"}).json()["made"]["id"]
    client.put(f"/api/screens/{sid}/cells/0", json={"session": "a"})
    before = screens.path().read_text("utf-8")
    kwargs = {"json": body} if body is not None else {}
    r = getattr(client, method)(path.format(sid=sid), **kwargs)
    assert r.status_code == status and reason in r.json()["error"]
    assert screens.path().read_text("utf-8") == before


def test_순서를_바꾸고_빠진_화면은_뒤에_남긴다(client):
    ids = [client.post("/api/screens", json={"name": n}).json()["made"]["id"] for n in "가나다"]
    r = client.put("/api/screens/order", json={"ids": [ids[2], ids[0]]})
    assert [s["name"] for s in r.json()["screens"]] == ["다", "가", "나"]


# --- 칸의 세션 줄 ---


def test_세션_줄에는_하는_일과_속한_프로젝트가_실린다(client, app, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    other = tmp_path / "other"
    other.mkdir()
    key = records.add_project(str(repo))["key"]
    app.state.claude.base = [
        row("s1", "안쪽", repo),  # 작업 폴더로 자동
        row("s2", "바깥", other),  # 등록 안 된 폴더 — 링크는 글자로
        row("s3", "옮김", other, project=key),  # 사람이 이 프로젝트로 정함
        row("s4", "뺌", repo, project=""),  # 사람이 '넣지 않음'
        row("s5", "지운 프로젝트", other, project="없는키"),
    ]
    out = {r["id"]: r for r in client.get("/api/screens/sessions").json()}
    assert [out[s]["project"] for s in ("s1", "s2", "s3", "s4", "s5")] == [
        key,
        None,
        key,
        None,
        None,
    ]
    assert out["s1"]["activity"] == "working" and out["s1"]["tokens"] == 1200
    assert out["s1"]["group"] == "업무" and out["s1"]["state"] == "on"


# --- 대화 응답 기억 ---


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    path = tmp_path / "conv.jsonl"
    lines = [
        {"type": "user", "message": {"content": "질문"}, "timestamp": "2026-11-27T00:00:00Z"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "답"}]},
            "timestamp": "2026-11-27T00:00:01Z",
        },
    ]
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", "utf-8")
    monkeypatch.setattr(chat, "transcript", lambda conv: path if conv == "s1-conv" else None)
    monkeypatch.setattr(claude_routes, "_chat_cache", claude_routes.OrderedDict())
    return path


def test_대화는_기록이_그대로면_다시_읽지_않는다(client, app, transcript, monkeypatch):
    """화면분할 4칸이 5초마다 묻는다 — 대화가 그대로면 기록을 다시 훑지 않는다."""
    app.state.claude.base = [row("s1", "안쪽", transcript.parent)]
    calls = []
    real = chat.chat
    monkeypatch.setattr(chat, "chat", lambda p, limit: calls.append(limit) or real(p, limit))
    first = client.get("/api/claude/sessions/s1/chat?limit=10").json()
    assert client.get("/api/claude/sessions/s1/chat?limit=10").json() == first
    assert calls == [10]
    client.get("/api/claude/sessions/s1/chat?limit=20")  # 더 보기는 따로
    assert calls == [10, 20]
    with transcript.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"content": "새 질문"}}) + "\n")
    texts = [
        m["text"] for m in client.get("/api/claude/sessions/s1/chat?limit=10").json()["messages"]
    ]
    assert calls == [10, 20, 10] and texts[-1] == "새 질문"


def test_작업_상자의_원본이_바뀌면_다시_붙인다(client, app, transcript, tmp_path, monkeypatch):
    key = records.add_project(str(tmp_path))["key"]
    app.state.claude.base = [row("s1", "안쪽", tmp_path)]
    calls = []
    monkeypatch.setattr(records, "chat_work", lambda k, messages: calls.append(k))
    url = f"/api/claude/sessions/s1/chat?limit=10&project={key}"
    client.get(url)
    client.get(url)
    assert calls == [key]
    records.store.append_activity(key, {"op": "edit", "path": "a.py", "prompt": "p"})
    client.get(url)
    assert calls == [key, key]
