"""Claude 세션 관리 테스트.

실제 claude 명령·프로세스 종료·터미널 열기는 부르지 않는다. 모두 이 PC 의 실제 세션을
건드리기 때문이다. 대신 그 결과를 흉내 내 판단 로직만 확인한다.
여기 시험하는 판단은 모두 실제로 겪은 문제에서 나왔다 (복사본·옛 대화 복구·하위 세션 오인 등).
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devdash.api import claude_routes
from devdash.claude import agents, hooks, service
from devdash.claude.commands import drift_note
from devdash.claude.registry import by_group, find, load, norm, save
from devdash.claude.service import SessionService
from devdash.local.windows import parse_tasklist

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"


def entry(sid=SID_A, name="alpha", group="G", project="C:/p") -> dict:
    return {"id": sid, "name": name, "group": group, "project": project}


# --- 목록 파일 ---


def test_목록은_없으면_비어_있고_저장하면_다시_읽힌다(tmp_path):
    path = tmp_path / "sessions.json"
    assert load(path) == []
    save([entry()], path)
    assert load(path) == [entry()]


def test_번호_앞자리나_이름으로_찾는다():
    entries = [entry(), entry(SID_B, "beta")]
    assert find(entries, "aaaa") == [entries[0]]
    assert find(entries, "beta") == [entries[1]]
    assert find(entries, "zzz") == []


def test_그룹은_가나다순_그룹_안은_이름순이고_대소문자를_가리지_않는다():
    entries = [
        entry(name="b", group="web"),
        entry(name="A", group="WEB2"),
        entry(name="a", group="Data"),
    ]
    assert [(g, [e["name"] for e in es]) for g, es in by_group(entries)] == [
        ("Data", ["a"]),
        ("web", ["b"]),
        ("WEB2", ["A"]),
    ]


def test_경로_표기를_맞춘다():
    assert norm("C:\\Projects\\RAG\\") == "C:/Projects/RAG"


# --- 실행 중 세션 찾기 ---


def test_pid_가_없는_기록은_살아있다고_보지_않는다():
    """목록에 있어도 pid 가 없으면 안 도는 기록이다. 살아있다고 보면 복구를 건너뛴다."""
    live = [{"sessionId": SID_A, "name": "alpha", "pid": None}]
    assert agents.find_live(entry(), live) is None


def test_번호가_바뀌어도_이름으로_찾는다():
    """/clear 나 복구로 번호가 바뀐 세션도 같은 세션으로 본다."""
    live = [{"sessionId": SID_B, "name": "alpha", "pid": 1}]
    assert agents.find_live(entry(), live) == live[0]


def test_번호가_어긋나면_갱신_안내를_낸다():
    assert drift_note(entry(), {"sessionId": SID_A}) is None
    note = drift_note(entry(), {"sessionId": SID_B})
    assert "aaaaaaaa → 실제 bbbbbbbb" in note


# --- 이어 띄우기 결과 해석 ---


def test_신뢰하지_않은_폴더는_승인_방법을_알려준다():
    """폴더 이름을 바꾸면 Claude 에겐 처음 보는 폴더라 다시 승인해야 한다."""
    msg = agents.explain_resume(1, "Workspace not trusted. Run `claude` in C:\\x once")
    assert "신뢰" in msg


def test_복사본이_생기면_실패로_본다():
    """종료 코드는 0 이어도 원본을 잇지 못했으면 실패다."""
    assert "복사본" in agents.explain_resume(0, "note: ... so this started a copy as 04355b16.")


def test_그_밖의_실패는_첫_줄을_돌려주고_성공은_None():
    assert agents.explain_resume(2, "\n  boom\nmore") == "boom"
    assert agents.explain_resume(0, "backgrounded · aaaaaaaa") is None


def test_Claude_세션_표시를_지우고_사용자_설정은_남긴다(monkeypatch):
    """이 표시를 물려받은 claude 는 하위 세션으로 오인해 대화 기록 저장을 끈다."""
    for k in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_JOB_DIR", "CLAUDE_PID"):
        monkeypatch.setenv(k, "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "keep")
    agents.drop_claude_markers()
    assert not any(
        k in os.environ
        for k in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_JOB_DIR", "CLAUDE_PID")
    )
    assert os.environ["CLAUDE_CONFIG_DIR"] == "keep"


# --- /clear 훅 ---


@pytest.fixture
def hook_paths(tmp_path):
    sessions = tmp_path / "sessions.json"
    save([entry()], sessions)
    return {
        "pending": tmp_path / "pending.json",
        "sessions": sessions,
        "log": tmp_path / "hooks.log",
    }


def clear(hook_paths, old, new, cwd="C:\\p", end_cwd=None):
    hooks.on_end(
        {"session_id": old, "cwd": end_cwd or cwd, "reason": "clear"},
        hook_paths["pending"],
        hook_paths["log"],
    )
    hooks.on_start(
        {"session_id": new, "cwd": cwd, "source": "clear"},
        hook_paths["pending"],
        hook_paths["sessions"],
        hook_paths["log"],
    )


def test_clear_하면_목록_번호가_새_번호로_바뀐다(hook_paths):
    clear(hook_paths, SID_A, SID_B)
    assert load(hook_paths["sessions"])[0]["id"] == SID_B


def test_목록에_없는_세션의_clear_는_건드리지_않는다(hook_paths):
    clear(hook_paths, "cccccccc-x", SID_B)
    assert load(hook_paths["sessions"])[0]["id"] == SID_A
    assert "목록에 없음" in hook_paths["log"].read_text(encoding="utf-8")


def test_폴더가_다르면_짝짓지_않는다(hook_paths):
    clear(hook_paths, SID_A, SID_B, cwd="C:\\p", end_cwd="C:\\other")
    assert load(hook_paths["sessions"])[0]["id"] == SID_A


def test_clear_가_아닌_시작과_끝은_무시한다(hook_paths):
    hooks.on_end(
        {"session_id": SID_A, "cwd": "C:\\p", "reason": "logout"},
        hook_paths["pending"],
        hook_paths["log"],
    )
    assert not hook_paths["pending"].exists()


def test_오래된_맡김은_쓰지_않는다(hook_paths, monkeypatch):
    hooks.on_end(
        {"session_id": SID_A, "cwd": "C:\\p", "reason": "clear"},
        hook_paths["pending"],
        hook_paths["log"],
    )
    later = time.time() + hooks.PENDING_TTL + 1
    monkeypatch.setattr(hooks.time, "time", lambda: later)
    hooks.on_start(
        {"session_id": SID_B, "cwd": "C:\\p", "source": "clear"},
        hook_paths["pending"],
        hook_paths["sessions"],
        hook_paths["log"],
    )
    assert load(hook_paths["sessions"])[0]["id"] == SID_A


# --- PC 메모리 ---


def test_tasklist_를_앱_이름별로_합친다():
    text = (
        '"chrome.exe","1","Console","1","100,000 K"\n'
        '"chrome.exe","2","Console","1","50.000 K"\n'  # 구분 기호가 다른 지역 설정
        '"Messenger 4.0.exe","3","Console","1","1 K"\n'
        "broken line\n"
    )
    assert parse_tasklist(text) == {"chrome": 150_000 * 1024, "Messenger 4.0": 1024}


# --- 버튼 동작과 API ---


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """실제 claude·프로세스·터미널을 부르지 않는 서비스."""
    path = tmp_path / "sessions.json"
    project = tmp_path / "proj"
    project.mkdir()
    save([entry(project=str(project)), entry(SID_B, "beta", project=str(project))], path)
    live: list[dict] = []
    calls: list[tuple] = []
    monkeypatch.setattr(agents, "live_sessions", lambda: live)
    monkeypatch.setattr(agents, "in_roster", lambda sid: False)
    monkeypatch.setattr(agents, "bg_task_count", lambda rec: 1)
    monkeypatch.setattr(service, "kill_tree", lambda pid: calls.append(("kill", pid)))
    monkeypatch.setattr(
        service, "open_terminal", lambda cwd, title, args: calls.append(("term", args))
    )
    s = SessionService(path)
    # 연결은 뒤에서 도는 일이라, 시험에서는 돌리지 않고 무엇을 돌릴지만 적는다
    s._background = lambda fn, *args: calls.append(("bg", fn.__name__, args))
    s.live, s.calls = live, calls
    return s


def test_연결하면_연결_중이_되고_다시_누르면_거절한다(svc):
    e = svc.entry(SID_A)
    assert svc.start(e) is None
    assert {r["name"]: r["state"] for r in svc.rows()}["alpha"] == "starting"
    assert svc.start(e) == "이미 처리 중입니다"


def test_모두_연결은_꺼진_것만_순서대로_대상으로_삼는다(svc):
    svc.live.append({"sessionId": SID_B, "name": "beta", "pid": 9, "kind": "background"})
    assert svc.start_all() is None
    targets = next(c for c in svc.calls if c[0] == "bg")[2][0]
    assert [t["name"] for t in targets] == ["alpha"]
    assert svc.start_all() == "연결할 꺼진 세션이 없습니다"


def test_답을_만드는_중이면_해제를_거절한다(svc):
    svc.live.append(
        {
            "sessionId": SID_A,
            "name": "alpha",
            "pid": 9,
            "kind": "background",
            "state": "working",
            "status": "busy",
        }
    )
    assert "작업 중" in svc.stop(svc.entry(SID_A))
    assert ("kill", 9) not in svc.calls


def test_백그라운드_작업만_돌면_확인을_받고_끈다(svc):
    """tichu-manager 처럼 서버를 띄워 둔 세션은 채팅이 멈춰 있어도 busy 다."""
    svc.live.append(
        {
            "id": "aaaaaaaa",
            "sessionId": SID_A,
            "name": "alpha",
            "pid": 9,
            "kind": "background",
            "state": "done",
            "status": "busy",
        }
    )
    e = svc.entry(SID_A)
    assert "confirm" in svc.stop(e)
    assert svc.stop(e, force=True) is None
    assert ("kill", 9) in svc.calls


def test_터미널_세션은_해제하지_않는다(svc):
    svc.live.append({"sessionId": SID_A, "name": "alpha", "pid": 9, "kind": "interactive"})
    assert "터미널" in svc.stop(svc.entry(SID_A))


def test_포그라운드는_꺼졌으면_이어_열고_켜졌으면_붙는다(svc):
    """켜진 대화를 포그라운드로 또 열면 복사본이 생겨 붙기만 한다."""
    assert svc.foreground(svc.entry(SID_A)) is None
    assert ("term", ["claude", "--resume", SID_A]) in svc.calls
    svc.live.append(
        {"id": "bbbbbbbb", "sessionId": SID_B, "name": "beta", "pid": 9, "kind": "background"}
    )
    assert svc.foreground(svc.entry(SID_B)) is None
    assert ("term", ["claude", "attach", "bbbbbbbb"]) in svc.calls


def test_API_는_실패를_error_확인을_confirm_으로_409_에_담는다(svc):
    app = FastAPI()
    app.state.claude = svc
    app.include_router(claude_routes.router)
    client = TestClient(app)

    rows = client.get("/api/claude/sessions").json()
    assert [r["name"] for r in rows] == ["alpha", "beta"]

    assert client.post(f"/api/claude/sessions/{SID_A}/start").json() == {"ok": True}
    r = client.post(f"/api/claude/sessions/{SID_A}/start")
    assert r.status_code == 409 and r.json() == {"error": "이미 처리 중입니다"}

    svc.live.append(
        {
            "id": "bbbbbbbb",
            "sessionId": SID_B,
            "name": "beta",
            "pid": 9,
            "kind": "background",
            "state": "done",
            "status": "busy",
        }
    )
    r = client.post(f"/api/claude/sessions/{SID_B}/stop", json={})
    assert r.status_code == 409 and "confirm" in r.json()
    assert client.post(f"/api/claude/sessions/{SID_B}/stop", json={"force": True}).json() == {
        "ok": True
    }

    assert client.post("/api/claude/sessions/nope/start").status_code == 404


# --- 세션 목록을 고치는 길 (6단계 Fable 검토) ---


def test_목록은_잠근_채_고치고_통째로_바꿔_끼운다(tmp_path):
    """잠그지 않으면 /clear 훅과 화면이 동시에 고칠 때 한쪽 갱신이 사라지고,
    쓰는 도중 읽으면 반쯤 쓴 파일을 읽었다."""
    import threading

    from devdash.claude import registry

    path = tmp_path / "sessions.json"
    registry.save([{"id": f"s{i}", "name": f"n{i}"} for i in range(50)], path)
    errors = []

    def reader():
        for _ in range(300):
            try:
                registry.load(path)
            except (ValueError, PermissionError) as exc:
                errors.append(exc)

    def writer(tag):
        for i in range(30):
            with registry.editing(path) as entries:
                entries.append({"id": f"{tag}-{i}", "name": tag})

    threads = [threading.Thread(target=reader) for _ in range(2)]
    threads += [threading.Thread(target=writer, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [e for e in errors if isinstance(e, ValueError)] == []
    assert len(registry.load(path)) == 50 + 60  # 두 쪽 갱신이 하나도 사라지지 않았다
    assert not list(tmp_path.glob("*.tmp"))


def test_블록에서_예외가_나면_저장하지_않는다(tmp_path):
    from devdash.claude import registry

    path = tmp_path / "sessions.json"
    registry.save([{"id": "a", "name": "a"}], path)
    with pytest.raises(RuntimeError), registry.editing(path) as entries:
        entries.clear()
        raise RuntimeError
    assert registry.load(path) == [{"id": "a", "name": "a"}]
