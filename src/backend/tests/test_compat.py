"""호환 시험(devdash check-claude)의 검사 규칙 테스트.

실제 claude 를 돌리는 부분(Runner)은 로그인된 Claude 가 필요해 여기서 돌리지 않는다 (CI 에서도 뺀다).
여기서는 Claude 의 출력이 바뀌었을 때 — 필드 하나가 빠지는 식으로 — 어느 항목이 왜 실패했는지
한 줄로 나오는지 본다.
"""

from __future__ import annotations

import json

import pytest

from devdash import compat


def bg(**over) -> dict:
    row = {
        "id": "aaaaaaaa",
        "sessionId": "aaaaaaaa-1",
        "cwd": "C:/p",
        "kind": "background",
        "state": "done",
    }
    row.update(over)
    return row


# --- 1. 세션 목록 ---


def test_목록_모양이_맞으면_통과():
    interactive = {"kind": "interactive", "sessionId": "x-1", "cwd": "C:/p"}
    assert compat.check_rows([bg(pid=1), interactive], [bg(pid=1)]) is None


def test_목록에서_필드가_빠지면_어느_필드인지_말한다():
    broken = bg()
    del broken["sessionId"]
    assert compat.check_rows([], [broken]) == "--all 행(aaaaaaaa)에 sessionId 가 없다"


def test_대화형_행에_목록_번호가_생기면_실패한다():
    """대화형은 목록 번호가 없어 보이기만 한다 — 번호가 생기면 그 처리를 다시 봐야 한다."""
    reason = compat.check_rows([{"kind": "interactive", "id": "x", "sessionId": "x-1"}], [])
    assert "대화형 행에 목록 번호" in reason


def test_목록이_배열이_아니면_실패한다():
    assert compat.check_rows({"sessions": []}, []) == "목록이 JSON 배열이 아니다"


# --- 9. 훅 입력 ---


def hooks(**drop) -> dict:
    base = {
        "SessionStart": [
            {"source": "startup", "session_id": "s", "cwd": "C:/p", "session_title": "a"}
        ],
        "UserPromptSubmit": [{"session_id": "s", "prompt_id": "p"}],
        "PostToolUse": [{"tool_name": "Read", "tool_input": {}, "tool_response": {}}],
        "Stop": [{"last_assistant_message": "바나나", "stop_hook_active": False}],
        "SessionEnd": [{"reason": "clear"}],
    }
    for event, key in drop.items():
        if key is None:
            del base[event]
        else:
            del base[event][0][key]
    return base


def test_훅_입력이_맞으면_통과():
    assert compat.check_hooks(hooks()) is None


def test_훅이_안_오면_어느_훅인지_한꺼번에_말한다():
    assert compat.check_hooks(hooks(UserPromptSubmit=None, Stop=None)) == (
        "UserPromptSubmit, Stop 훅이 오지 않았다"
    )


def test_훅_입력에서_필드가_빠지면_어느_필드인지_말한다():
    assert compat.check_hooks(hooks(Stop="stop_hook_active")) == (
        "Stop 훅 입력에 stop_hook_active 가 없다"
    )


def test_clear_의_끝이_reason_clear_가_아니면_실패한다():
    captured = hooks()
    captured["SessionEnd"] = [{"reason": "other"}]
    assert "reason 이 clear" in compat.check_hooks(captured)


# --- 7 · 8. 대화 기록 ---


def lines() -> list[dict]:
    return [
        {"type": "user", "isSidechain": False, "timestamp": "t", "message": {}, "promptId": "p"},
        {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 1,
                    "cache_creation_input_tokens": 2,
                    "cache_read_input_tokens": 3,
                }
            },
        },
    ]


def test_대화_기록_모양이_맞으면_통과():
    assert compat.check_transcript(lines()) is None


def test_토큰_필드가_빠지면_실패한다():
    """세션 칸의 토큰 % 가 이 세 값을 더해 만든다."""
    broken = lines()
    del broken[1]["message"]["usage"]["cache_read_input_tokens"]
    assert compat.check_transcript(broken) == "usage 에 cache_read_input_tokens 가 없다"


def test_압축_기록에_postTokens_가_없으면_실패한다():
    compact = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {}}
    assert "postTokens" in compat.check_compactions([compact])
    compact["compactMetadata"]["postTokens"] = 1000
    assert compat.check_compactions([compact]) is None


def test_수정_결과의_모양():
    edit = {"filePath": "a.py", "oldString": "a", "newString": "b", "structuredPatch": []}
    write = {"filePath": "b.py", "type": "create", "content": "x"}
    assert compat.check_edit_results([edit, write]) is None
    del edit["structuredPatch"]
    assert compat.check_edit_results([edit]) == "Edit 결과에 newString · structuredPatch 가 없다"


# --- 10 · 14 · 6 ---


def test_플러그인_목록_모양():
    good_m = [{"name": "dev-dashboard", "path": "C:/r"}]
    good_p = [{"id": "devdash@dev-dashboard", "enabled": True}]
    assert compat.check_plugin_lists(good_m, good_p) is None
    assert "id · enabled" in compat.check_plugin_lists(good_m, [{"id": "x"}])


@pytest.mark.parametrize(
    ("text", "version"),
    [("2.1.328 (Claude Code)", "2.1.328"), ("claude 10.0.1", "10.0.1"), ("버전 없음", None)],
)
def test_버전_읽기(text, version):
    assert compat.parse_version(text) == version


def test_대화_기록_폴더_이름_규칙():
    assert compat.transcript_folder(r"C:\Users\me\.devdash\compat") == (
        "C--Users-me--devdash-compat"
    )


# --- 결과 파일 ---


def test_모두_통과하면_그_버전을_통과한_버전으로_남긴다():
    items = [compat.Item(1, "세션 목록", True), compat.Item(8, "수정 기록", None, "없음")]
    data = compat.save_results("2.1.328", items)
    assert data["passed"] and data["passedVersion"] == "2.1.328"
    saved = json.loads(compat.results_path().read_text(encoding="utf-8"))
    assert saved["items"][1]["reason"] == "없음"


def test_실패하면_마지막으로_통과한_버전을_그대로_둔다():
    compat.save_results("2.1.327", [compat.Item(1, "세션 목록", True)])
    data = compat.save_results("2.1.328", [compat.Item(5, "/clear", False, "이유")])
    assert not data["passed"] and data["passedVersion"] == "2.1.327"


def test_한_줄_요약():
    assert compat.summary_line(compat.Item(5, "/clear", False, "번호가 안 바뀌었다")) == (
        "  실패   5. /clear — 번호가 안 바뀌었다"
    )


def test_대시보드는_지금_버전과_통과한_버전을_본다(monkeypatch):
    monkeypatch.setattr(compat, "installed_version", lambda: "2.1.329")
    compat.save_results("2.1.328", [compat.Item(1, "세션 목록", True)])
    status = compat.status()
    assert status["version"] == "2.1.329" and status["passedVersion"] == "2.1.328"


# --- 버전이 바뀌면 자동 호환 시험 ---


@pytest.mark.parametrize(
    ("version", "results", "auto_error", "running", "expected"),
    [
        ("2.0.1", {"passedVersion": "2.0.0"}, {}, False, True),  # 새 버전
        ("2.0.0", {"passedVersion": "2.0.0"}, {}, False, False),  # 이미 통과
        ("2.0.1", {"passedVersion": "2.0.0"}, {}, True, False),  # 이미 도는 중 — 한 번에 하나
        ("2.0.1", {"passedVersion": "2.0.0", "version": "2.0.1"}, {}, False, False),  # 실패한 버전
        ("2.0.1", {"passedVersion": "2.0.0"}, {"version": "2.0.1"}, False, False),  # 시작 못 함
        (None, {}, {}, False, False),  # 버전을 못 읽음
    ],
)
def test_자동_호환_시험을_돌릴지(version, results, auto_error, running, expected):
    from devdash import compat_auto

    assert compat_auto.should_run(version, results, auto_error, running) is expected


def test_자동_호환_시험은_켠_경우에만_새_버전에서_돌린다(monkeypatch):
    from devdash import compat_auto, prefs

    ran = []
    monkeypatch.setattr(compat, "installed_version", lambda fresh=False: "2.0.1")
    monkeypatch.setattr(compat, "load_results", lambda: {"passedVersion": "2.0.0"})
    auto = compat_auto.AutoCompat(spawn=ran.append)
    assert auto.check_once() is False and ran == []  # 꺼져 있다 (setup 에서 아니오)
    prefs.put("autoCompat", True)
    assert auto.check_once() is True and ran == ["2.0.1"]


def test_시작하지_못하면_이유를_남기고_상태에_보인다(monkeypatch):
    monkeypatch.setattr(compat, "installed_version", lambda fresh=False: "2.0.1")
    compat.save_auto_error("2.0.1", "시험 폴더를 Claude 가 아직 신뢰하지 않았습니다")
    assert "신뢰" in compat.status()["autoError"]
    monkeypatch.setattr(compat, "installed_version", lambda fresh=False: "2.0.2")
    assert compat.status()["autoError"] is None  # 다른 버전의 이유는 보이지 않는다


def test_돌고_있는_시험이_있으면_상태에_보이고_손으로_또_돌리지_않는다(monkeypatch):
    import os

    compat.running_path().parent.mkdir(parents=True, exist_ok=True)
    compat.running_path().write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    monkeypatch.setattr(compat, "installed_version", lambda fresh=False: "2.0.1")
    assert compat.status()["running"] is True
    said = []
    assert compat.main(said.append) == 1 and "돌고 있습니다" in said[0]
    compat.running_path().write_text(json.dumps({"pid": 2**30}), encoding="utf-8")  # 끝난 프로세스
    assert compat.running() is None and not compat.running_path().exists()


def test_지금_버전에서_실패한_항목은_상태에_이유와_함께(monkeypatch):
    monkeypatch.setattr(compat, "installed_version", lambda fresh=False: "2.0.1")
    items = [compat.Item(1, "세션 목록", True), compat.Item(5, "/clear", False, "번호가 안 바뀜")]
    compat.save_results("2.0.1", items)
    assert compat.status()["failures"] == [
        {"num": 5, "title": "/clear", "reason": "번호가 안 바뀜"}
    ]


def test_시험_세션을_끌_때_명단은_처음_대화_번호로_본다(monkeypatch):
    """/clear 한 뒤에는 목록의 번호가 새 대화다 — 명단에는 처음 번호만 있어, 그 번호로 보면
    기다리지 않고 다음으로 넘어갔다."""
    runner = compat.Runner(lambda line: None)
    asked = []
    monkeypatch.setattr(compat.agents, "first_conversation", lambda sid: "first-1")
    monkeypatch.setattr(compat.agents, "in_roster", lambda conv: asked.append(conv) or False)
    monkeypatch.setattr(runner, "row", lambda sid: {"id": sid, "sessionId": "after-clear"})
    monkeypatch.setattr(runner, "claude", lambda *args, **kw: (0, ""))
    monkeypatch.setattr(compat.time, "sleep", lambda s: None)
    assert runner.kill("a") and runner.stop("a")
    assert asked == ["first-1", "first-1"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({}, None),
        ({"every": "first-1"}, "--all 의 sessionId 가 새 번호가 아니다"),
        ({"live": "first-1"}, "--json 의 sessionId 가 새 번호가 아니다"),
        ({"live": None}, "--json 의 sessionId 가 새 번호가 아니다"),
        ({"kept": "new-1"}, "state.json 의 sessionId 가 처음 번호가 아니다"),
        ({"rostered": False}, "관리자 명단에 처음 번호가 없다"),
    ],
)
def test_켜진_채_clear_한_뒤_번호가_있을_자리(change, reason):
    """대시보드가 기대는 것: 목록은 새 번호 · state.json 과 관리자 명단은 처음 번호."""
    ok = {"every": "new-1", "live": "new-1", "kept": "first-1", "rostered": True}
    problems = compat.check_after_clear("first-1", "new-1", **{**ok, **change})
    if reason is None:
        assert problems == []
    else:
        assert len(problems) == 1 and reason in problems[0]
