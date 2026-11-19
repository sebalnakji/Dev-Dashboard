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
