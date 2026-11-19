"""커밋 AI 요약 (T-11). 실제 claude 는 부르지 않고 실행 부분을 바꿔 끼운다."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from devdash import commit_summary

DETAIL = {
    "subject": "합계를 sum 으로",
    "author": "김동료",
    "body": "",
    "files": [
        {"status": "M", "path": "calc.py", "added": 1, "deleted": 4},
        {"status": "R", "path": "새.txt", "old": "옛.txt", "added": 0, "deleted": 0},
        {"status": "A", "path": "그림.png", "added": None, "deleted": None},
    ],
}


def test_부탁에는_커밋_메시지와_파일과_diff_가_들어가고_긴_diff_는_자른다():
    text = commit_summary.prompt(DETAIL, "x" * (commit_summary.DIFF_LIMIT + 10))
    assert "제목: 합계를 sum 으로" in text and "본문:\n(없음)" in text
    assert "M calc.py (+1 −4)" in text and "R 옛.txt → 새.txt (+0 −0)" in text
    assert "A 그림.png (글 아님)" in text
    assert f"앞부분 {commit_summary.DIFF_LIMIT:,}자만" in text
    assert "x" * (commit_summary.DIFF_LIMIT + 1) not in text


class FakeRun:
    """run_bounded 대역: (종료 코드, 출력, 오류) 또는 시간 초과면 None."""

    def __init__(self):
        self.result: tuple[int, str, str] | None = (0, "- 합계를 sum 으로 바꿨다", "")
        self.calls: list[dict] = []

    def __call__(self, args, **kw):
        self.calls.append({"args": args, **kw})
        return self.result


@pytest.fixture
def fake(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(commit_summary, "claude_exe", lambda: "claude")
    monkeypatch.setattr(commit_summary, "run_bounded", run)
    return run


def test_도구_없이_빈_폴더에서_sonnet_으로_돌린다(fake):
    assert commit_summary.summarize(DETAIL, "diff") == "- 합계를 sum 으로 바꿨다"
    (call,) = fake.calls
    args = call["args"]
    # 남이 쓴 코드 안의 지시를 따르지 못하게 도구를 하나도 주지 않는다
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--model") + 1] == "sonnet"
    assert "--no-session-persistence" in args
    assert args[args.index("--setting-sources") + 1] == ""
    assert "diff" in call["input_text"]
    # 임시 폴더는 끝나면 지워진다 (이 PC 의 파일을 보지 않게 빈 폴더에서 돈다)
    assert not Path(call["cwd"]).exists()


def test_실패하면_이유를_담아_알린다(fake):
    fake.result = (1, "", "Error: not logged in\n")
    with pytest.raises(commit_summary.SummaryError, match="not logged in"):
        commit_summary.summarize(DETAIL, "diff")
    fake.result = None  # 시간 초과 (run_bounded 가 트리를 끝내고 None)
    with pytest.raises(commit_summary.SummaryError, match="180초"):
        commit_summary.summarize(DETAIL, "diff")


def test_시간이_넘으면_그_프로세스_트리만_끝낸다():
    """손자 프로세스(cmd → 하위 프로그램)가 남아도 붙잡히지 않는다. 실제 프로세스로 본다."""
    import time

    from devdash.local import run_bounded

    start = time.time()
    # 자식이 손자를 띄워 10초 기다린다 (claude: cmd → node 처럼). 2초에 끝내야 한다
    grandchild = (
        "import subprocess, sys; "
        "subprocess.run([sys.executable, '-c', 'import time; time.sleep(10)'])"
    )
    assert run_bounded([sys.executable, "-c", grandchild], timeout=2) is None
    assert time.time() - start < 8
    echo = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    code, out, _ = run_bounded([sys.executable, "-c", echo], timeout=30, input_text="안녕\n")
    assert code == 0 and out == "안녕\n"
