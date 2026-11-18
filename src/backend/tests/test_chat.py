"""세션 대화 읽기 (프로젝트 탭 [세션] 보기, T-8).

Claude Code 의 대화 기록(jsonl)을 흉내 낸 파일로 본다. 실제 ~/.claude 는 읽지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devdash.api.claude_routes import router
from devdash.claude import chat

SID = "11111111-2222-3333-4444-555555555555"


def user(text, **extra):
    return {"type": "user", "timestamp": "t", "message": {"role": "user", "content": text}, **extra}


def answer(*blocks, **extra):
    return {"type": "assistant", "timestamp": "t", "message": {"content": list(blocks)}, **extra}


def text(t):
    return {"type": "text", "text": t}


def tool(name):
    return {"type": "tool_use", "name": name, "input": {}}


def result():
    return user([{"type": "tool_result", "content": "아주 긴 파일 내용 " * 50}])


def write(path: Path, entries: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", "utf-8")
    return path


@pytest.fixture
def log(tmp_path) -> Path:
    entries = [answer(text("앞의 답"))]
    for i in range(1, 26):
        entries += [
            user(f"질문 {i}"),
            answer({"type": "thinking", "thinking": "생각"}, tool("Read")),
            result(),
            answer(text(f"답 {i}-1"), tool("Edit")),
            result(),
            answer(text(f"답 {i}-2")),
        ]
    entries += [
        user("<task-notification>끝남</task-notification>"),
        user("알림", isMeta=True),
        user("서브에이전트", isSidechain=True),
        user("<command-name>/clear</command-name><command-args></command-args>"),
        user("<bash-input>git status</bash-input>"),
        user([text("이 그림 봐 줘"), {"type": "image", "source": {}}]),
        answer(text("마지막 답"), tool("Bash"), tool("Bash")),
        {"type": "attachment", "attachment": {}},
    ]
    return write(tmp_path / "proj" / f"{SID}.jsonl", entries)


def test_최근_질문과_답만_시간_순으로_주고_도구는_횟수만_센다(log):
    r = chat.chat(log, limit=3)
    assert [m["text"] for m in r["messages"]] == [
        "/clear",
        "! git status",
        "이 그림 봐 줘\n[이미지]",
        "마지막 답",
    ]
    assert r["messages"][-1]["tools"] == {"Bash": 2} and r["more"] is True


def test_한_차례의_답은_이어_붙이고_도구는_합친다(log):
    r = chat.chat(log, limit=4)
    q, a = r["messages"][0], r["messages"][1]
    assert (q["role"], q["text"]) == ("user", "질문 25")
    assert a["text"] == "답 25-1\n\n답 25-2" and a["tools"] == {"Read": 1, "Edit": 1}


def test_더_보기는_더_이전_질문까지_주고_맨_앞이면_더_없다(log):
    assert chat.chat(log, limit=27)["more"] is True  # 질문은 모두 28개
    assert chat.chat(log, limit=28)["more"] is False
    r = chat.chat(log, limit=200)
    users = [m for m in r["messages"] if m["role"] == "user"]
    assert len(users) == 28 and r["more"] is False
    assert r["messages"][0]["text"] == "앞의 답"  # 첫 질문 앞의 답도 버리지 않는다


def test_덩어리_경계에_걸린_줄도_온전히_읽는다(log, monkeypatch):
    """끝에서부터 덩어리로 읽으니, 덩어리가 아주 작아 줄이 여러 번 잘려도 결과가 같아야 한다."""
    whole = chat.chat(log, limit=200)
    monkeypatch.setattr(chat, "CHUNK", 97)
    assert chat.chat(log, limit=200) == whole


def test_세션_목록_요약은_최근_질문들의_답_시각이다(log):
    s = chat.summary(log)
    # 최근 질문 20개(질문 9~25 · /clear · ! git status · 그림) 중 답이 있는 것은 18개
    assert len(s["replies"]) == 18 and s["lastAt"] == "t"


def usage(inp, created, read, **extra):
    return answer(
        text("답"),
        **{
            "message": {
                "content": [text("답")],
                "usage": {
                    "input_tokens": inp,
                    "cache_creation_input_tokens": created,
                    "cache_read_input_tokens": read,
                },
            }
        },
        **extra,
    )


def boundary(post=None):
    e = {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"}
    if post is not None:
        e["compactMetadata"] = {"trigger": "manual", "preTokens": 900_000, "postTokens": post}
    return e


def test_압축이_마지막_답보다_최근이면_요약에_기본분을_더해_어림한다(tmp_path):
    """/clear 시험에서 확인: 압축 직후에도 다음 답이 나올 때까지 압축 전 %가 남았다.
    postTokens 는 대화 요약만 세므로 시스템 프롬프트 · 도구 같은 기본분을 더한다."""
    # 지난 압축 뒤 첫 답에서 기본분을 잰다: 70_000 - 10_000 = 60_000
    entries = [
        user("q"),
        usage(0, 0, 50_000),
        boundary(post=10_000),
        user("q"),
        usage(0, 0, 70_000),
    ]
    log = write(
        tmp_path / "p.jsonl", [*entries, user("q"), usage(0, 0, 900_000), boundary(post=16_053)]
    )
    assert chat.context_tokens(log) == 16_053 + 60_000
    # 아직 잰 적이 없으면(첫 압축) 세션 첫 답의 입력을 기본분으로
    first = write(
        tmp_path / "f.jsonl",
        [user("q"), usage(0, 0, 45_000), user("q"), usage(0, 0, 900_000), boundary(post=8_000)],
    )
    assert chat.context_tokens(first) == 8_000 + 45_000
    # 압축 뒤에 답이 나오면 그 답의 입력을 쓴다
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(user("q2")) + "\n" + json.dumps(usage(0, 1_000, 20_000)) + "\n")
    assert chat.context_tokens(log) == 21_000


def test_컨텍스트_토큰은_마지막_답의_입력이고_서브에이전트는_뺀다(tmp_path):
    log = write(
        tmp_path / "c.jsonl",
        [
            user("q"),
            usage(10, 20, 300),
            user("q2"),
            usage(1, 2, 400_000),
            usage(5, 5, 5, isSidechain=True),
        ],
    )
    assert chat.context_tokens(log) == 400_003
    assert chat.context_tokens(write(tmp_path / "d.jsonl", [user("q")])) is None


def test_압축_횟수는_덧붙은_부분만_읽어_센다(tmp_path):
    # 글 속에 같은 낱말이 있어도(따옴표가 이스케이프된다) 세지 않는다
    log = write(
        tmp_path / "e.jsonl",
        [user('"subtype":"compact_boundary" 이라고 써 봄'), boundary(), user("q")],
    )
    assert chat.compactions(log) == 1
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(boundary()) + "\n" + json.dumps(boundary()))  # 마지막 줄은 아직 쓰는 중
    assert chat.compactions(log) == 2  # 쓰는 중인 줄은 다음에 센다
    with open(log, "a", encoding="utf-8") as f:
        f.write("\n")
    assert chat.compactions(log) == 3


def test_번호_모양이_아니면_찾지_않고_작업용_복사본_폴더에서도_찾는다(tmp_path):
    write(tmp_path / "C--repo--claude-worktrees-x" / f"{SID}.jsonl", [user("안녕")])
    assert chat.transcript(SID, root=tmp_path).parent.name == "C--repo--claude-worktrees-x"
    assert chat.transcript("../../etc/passwd", root=tmp_path) is None
    assert chat.transcript("22222222-2222-3333-4444-555555555555", root=tmp_path) is None


class FakeSessions:
    """라우트가 쓰는 것은 '지금 보이는 세션의 대화 번호 찾기'(conversation_of) 뿐이다."""

    def __init__(self, known: dict[str, str]):
        self.known = known

    def conversation_of(self, key: str) -> str | None:
        return self.known.get(key)


def test_세션_목록에_있는_세션만_대화를_준다(tmp_path, log, monkeypatch):
    monkeypatch.setattr(chat, "PROJECTS", tmp_path)
    app = FastAPI()
    app.state.claude = FakeSessions({"aaaaaaaa": SID})  # 목록 번호 → 대화 번호
    app.include_router(router)
    client = TestClient(app)
    r = client.get("/api/claude/sessions/aaaaaaaa/chat", params={"limit": 1})
    assert r.status_code == 200 and r.json()["messages"][-1]["text"] == "마지막 답"
    other = "99999999-2222-3333-4444-555555555555"
    assert client.get(f"/api/claude/sessions/{other}/chat").status_code == 404
