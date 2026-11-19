"""Claude 세션 관리 테스트.

실제 claude 명령 · 프로세스 종료 · 터미널 열기는 부르지 않는다. 모두 이 PC 의 실제 세션을
건드리기 때문이다. 대신 `claude agents` 목록과 명령 결과를 흉내 내 판단 로직만 확인한다.
여기 시험하는 판단은 모두 실제로 겪은 문제에서 나왔다 (복사본 · /clear 뒤 이름을 잃음 · 하위 세션 오인 등).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devdash.api import claude_routes
from devdash.claude import agents, chat, registry, service
from devdash.claude.service import SessionService
from devdash.local.windows import parse_tasklist

ID_A, ID_B, ID_C = "aaaaaaaa", "bbbbbbbb", "cccccccc"
FIRST_A = "aaaaaaaa-1111-2222-3333-444444444444"
"""A 의 처음 대화 번호 (--all 의 sessionId)."""
AFTER_CLEAR_A = "dddddddd-1111-2222-3333-444444444444"
"""A 가 /clear 한 뒤의 대화 번호 (켜진 목록의 sessionId)."""
FIRST_B = "bbbbbbbb-1111-2222-3333-444444444444"
FIRST_C = "cccccccc-1111-2222-3333-444444444444"


def bg(sid, conversation, name, cwd, **kw) -> dict:
    """claude agents 의 백그라운드 행."""
    return {
        "id": sid,
        "sessionId": conversation,
        "name": name,
        "cwd": cwd,
        "kind": "background",
        **kw,
    }


# --- 배치 파일 (registry) ---


def test_배치는_없거나_옛_형식이면_비어_있다(tmp_path):
    path = tmp_path / "sessions.json"
    assert registry.load(path) == registry.empty()
    path.write_text('{"sessions": [{"id": "x", "name": "옛 등록"}]}', encoding="utf-8")
    assert registry.load(path) == registry.empty()


def test_그룹_만들기_이름_바꾸기_지우기():
    data = registry.empty()
    registry.add_group(data, " LRS ")
    with pytest.raises(ValueError):
        registry.add_group(data, "LRS")
    registry.place(data, ID_A, "LRS", [ID_A])
    registry.rename_group(data, "LRS", "회사")
    assert registry.group_of(data, ID_A) == "회사"
    registry.remove_group(data, "회사")
    # 세션은 지우지 않고 "분류 안 됨"으로
    assert registry.group_of(data, ID_A) is None and ID_A in data["sessions"]


def test_옮기면_그_그룹의_순서를_다시_매긴다():
    data = registry.empty()
    registry.add_group(data, "G")
    registry.place(data, ID_A, "G", [ID_A, ID_B])
    registry.place(data, ID_C, "G", [ID_C, ID_A, ID_B])
    assert [registry.order_of(data, s) for s in (ID_C, ID_A, ID_B)] == [0, 1, 2]
    with pytest.raises(ValueError):
        registry.place(data, ID_A, "없는 그룹", [ID_A])


def test_삭제_복원_정보는_30일_뒤에_지운다():
    data = registry.empty()
    now = time.time()
    data["deleted"] = [
        {"id": "old", "deletedAt": now - registry.KEEP_DELETED - 1},
        {"id": "new", "deletedAt": now - 3600},
    ]
    registry.prune_deleted(data, now)
    assert [d["id"] for d in data["deleted"]] == ["new"]


def test_배치는_잠근_채_고치고_통째로_바꿔_끼운다(tmp_path):
    """잠그지 않으면 화면의 두 요청이 동시에 고칠 때 한쪽 갱신이 사라진다."""
    path = tmp_path / "sessions.json"
    registry.save(registry.empty(), path)
    errors: list[Exception] = []

    def add(prefix: str) -> None:
        try:
            for i in range(30):
                with registry.editing(path) as data:
                    data["sessions"][f"{prefix}{i}"] = {"order": i}
        except Exception as exc:  # pragma: no cover - 실패할 때만 본다
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(p,)) for p in "xy"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(registry.load(path)["sessions"]) == 60


def test_블록에서_예외가_나면_저장하지_않는다(tmp_path):
    path = tmp_path / "sessions.json"
    registry.save(registry.empty(), path)
    with pytest.raises(RuntimeError), registry.editing(path) as data:
        registry.add_group(data, "G")
        raise RuntimeError
    assert registry.load(path)["groups"] == []


# --- claude 명령 결과 해석 ---


def test_신뢰하지_않은_폴더는_승인_방법을_알려준다():
    assert "신뢰" in agents.explain(1, "Error: Workspace not trusted")


def test_복사본이_생기면_실패로_본다():
    assert "복사본" in agents.explain(0, "started a copy of session aaaa")


def test_그_밖의_실패는_첫_줄을_돌려주고_성공은_None():
    assert agents.explain(2, "\n  boom\nmore") == "boom"
    assert agents.explain(0, "ok") is None


def test_Claude_세션_표시를_지우고_사용자_설정은_남긴다():
    env = {
        "CLAUDECODE": "1",
        "CLAUDE_CODE_CHILD_SESSION": "1",
        "CLAUDE_JOB_DIR": "x",
        "CLAUDE_PID": "1",
        "CLAUDE_CONFIG_DIR": "C:/cfg",
        "PATH": "p",
    }
    assert agents.clean_env(env) == {"CLAUDE_CONFIG_DIR": "C:/cfg", "PATH": "p"}


@pytest.fixture
def ran(monkeypatch):
    """subprocess.run 을 흉내 내 claude 에 넘긴 인자 · 폴더 · 환경을 적는다."""
    calls: list[dict] = []

    def fake_run(args, **kw):
        calls.append({"args": args[1:], "cwd": kw.get("cwd"), "env": kw.get("env")})
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(agents, "claude_exe", lambda: "claude")
    monkeypatch.setattr(agents.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    return calls


def test_새_세션은_그_폴더에서_이름과_원격_제어를_붙여_백그라운드로_띄운다(ran):
    """원격 제어 없이 만든 세션은 Claude 앱 사이드바에 보이지 않는다."""
    assert agents.create("C:/proj", "새 세션") is None
    call = ran[0]
    assert call["args"] == ["--bg", "--name", "새 세션", "--remote-control"]
    assert call["cwd"] == "C:/proj"
    # 표시를 물려받으면 새 claude 가 하위 세션으로 여겨 기록을 남기지 않는다
    assert "CLAUDE_CODE_CHILD_SESSION" not in call["env"]


def test_이어_열기는_전체_번호에_resume_만_붙인다(ran):
    """--name 같은 플래그를 붙이거나 번호를 줄이면 원본 대신 복사본이 생긴다."""
    agents.resume("C:/proj", FIRST_A)
    assert ran[0]["args"] == ["--bg", "--resume", FIRST_A] and ran[0]["cwd"] == "C:/proj"


def test_마지막_대화_번호는_clear_뒤에_바뀌는_resumeSessionId_에서(tmp_path, monkeypatch):
    """/clear 하면 sessionId 는 처음 번호로 남고 resumeSessionId 만 새 번호가 된다."""
    monkeypatch.setattr(agents, "JOBS", tmp_path)
    (tmp_path / ID_A).mkdir()
    state = {"sessionId": FIRST_A, "resumeSessionId": AFTER_CLEAR_A}
    (tmp_path / ID_A / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert agents.last_conversation(ID_A) == AFTER_CLEAR_A
    (tmp_path / ID_A / "state.json").write_text(json.dumps({"sessionId": FIRST_A}), "utf-8")
    assert agents.last_conversation(ID_A) == FIRST_A
    assert agents.last_conversation("없음") is None


def test_복원은_지울_때_적어_둔_실행_옵션을_다시_붙인다(ran):
    """지운 뒤라 원본이 없어 옵션을 붙여도 복사본이 생기지 않는다.
    --name 이 빠지면 Claude 가 스스로 다시 띄울 때 이름이 번호로 바뀐다."""
    agents.restore("C:/proj", AFTER_CLEAR_A, ["--name", "alpha", "--model", "haiku"], "alpha")
    assert ran[0]["args"] == [
        "--bg",
        "--resume",
        AFTER_CLEAR_A,
        "--name",
        "alpha",
        "--model",
        "haiku",
        "--remote-control",
    ]


def test_옛_복원_정보는_원격_제어와_이름을_더한다(ran):
    agents.restore("C:/proj", AFTER_CLEAR_A, [], "alpha")
    assert ran[0]["args"] == [
        "--bg",
        "--resume",
        AFTER_CLEAR_A,
        "--remote-control",
        "--name",
        "alpha",
    ]
    agents.restore("C:/proj", AFTER_CLEAR_A, [], None)
    assert ran[1]["args"] == ["--bg", "--resume", AFTER_CLEAR_A, "--remote-control"]


def test_신뢰_폴더는_승인한_것만_있는_폴더만(tmp_path, monkeypatch):
    ok, no = tmp_path / "ok", tmp_path / "no"
    ok.mkdir()
    no.mkdir()
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    projects = {
        str(ok): {"hasTrustDialogAccepted": True},
        str(no): {},
        "Z:/없음": {"hasTrustDialogAccepted": True},
    }
    (cfg / ".claude.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    assert agents.trusted_folders() == [str(ok)]


# --- PC 메모리 ---


def test_tasklist_를_앱_이름별로_합친다():
    text = (
        '"chrome.exe","1","Console","1","100,000 K"\n'
        '"chrome.exe","2","Console","1","50.000 K"\n'  # 구분 기호가 다른 지역 설정
        '"Messenger 4.0.exe","3","Console","1","1 K"\n'
        "broken line\n"
    )
    assert parse_tasklist(text) == {"chrome": 150_000 * 1024, "Messenger 4.0": 1024}


# --- 세션 칸 (서비스) ---


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """실제 claude · 프로세스 · 터미널을 부르지 않는 서비스.

    every = `agents --all` 행, live = `agents --json` 행. 시험이 고치면 다음 조회에 반영된다."""
    project = tmp_path / "proj"
    project.mkdir()
    cwd = str(project)
    every = [
        bg(ID_A, FIRST_A, "alpha", cwd, state="done"),
        bg(ID_B, FIRST_B, "beta", cwd, state="done"),
        bg(ID_C, FIRST_C, None, cwd, state="done"),  # Claude 쪽 이름이 빈 세션
    ]
    live: list[dict] = []
    calls: list[tuple] = []
    monkeypatch.setattr(chat, "PROJECTS", tmp_path / "claude-projects")
    monkeypatch.setattr(agents, "all_sessions", lambda: every)
    monkeypatch.setattr(agents, "live_sessions", lambda: live)
    monkeypatch.setattr(agents, "in_roster", lambda conversation: False)
    monkeypatch.setattr(agents, "bg_task_count", lambda sid: 1)
    monkeypatch.setattr(agents, "last_conversation", lambda sid: None)
    monkeypatch.setattr(agents, "job_state", lambda sid: None)
    monkeypatch.setattr(agents, "respawn_flags", lambda sid: [])
    monkeypatch.setattr(service, "kill_tree", lambda pid: calls.append(("kill", pid)))
    monkeypatch.setattr(
        service, "open_terminal", lambda cwd, title, args: calls.append(("term", args))
    )
    monkeypatch.setattr(
        agents, "resume", lambda cwd, conv: calls.append(("resume", cwd, conv)) or None
    )
    monkeypatch.setattr(agents, "confirm_started", lambda sid, limit=25: None)
    s = SessionService(tmp_path / "sessions.json")
    # 오래 걸리는 일은 뒤에서 도는 일이라, 시험에서는 돌리지 않고 무엇을 돌릴지만 적는다
    s._background = lambda fn, *args: calls.append(("bg", fn, args))
    # 목록을 2초 기억하면 시험이 고친 목록이 안 보인다
    monkeypatch.setattr(service, "LIST_CACHE_SECONDS", -1)
    s.every, s.live, s.calls, s.cwd = every, live, calls, cwd
    return s


def run_background(svc) -> None:
    """적어 둔 뒤의 일을 지금 돌린다."""
    pending = [c for c in svc.calls if c[0] == "bg"]
    svc.calls[:] = [c for c in svc.calls if c[0] != "bg"]
    for _, fn, args in pending:
        fn(*args)


def names(svc) -> dict:
    return {r["name"]: r for r in svc.rows()}


def test_등록하지_않아도_all_의_세션이_모두_보인다(svc):
    rows = names(svc)
    # 오래 꺼진 세션은 --all 에만 state done 으로 있다. 상태로 거르지 않는다
    assert set(rows) == {"alpha", "beta", "proj · cccccccc"}
    assert all(r["state"] == "off" and r["group"] is None for r in rows.values())
    assert rows["proj · cccccccc"]["named"] is False


def test_대화형_세션은_따로_보이고_그룹이_없다(svc):
    svc.live.append(
        {"sessionId": "eeee-1", "name": "터미널", "cwd": svc.cwd, "kind": "interactive"}
    )
    view = svc.view()
    assert [r["name"] for r in view["interactive"]] == ["터미널"]
    assert view["interactive"][0]["state"] == "terminal"
    assert "터미널" not in {r["name"] for r in view["sessions"]}


def test_clear_로_대화_번호가_바뀌어도_목록_번호로_그룹을_지킨다(svc):
    assert svc.edit(lambda d: registry.add_group(d, "G")) is None
    assert svc.place(ID_A, "G", [ID_A]) is None
    # /clear: 목록 번호 · 이름은 그대로, 켜진 목록의 대화 번호만 바뀐다
    svc.live.append(bg(ID_A, AFTER_CLEAR_A, "alpha", svc.cwd, pid=9, state="done"))
    row = names(svc)["alpha"]
    assert row["group"] == "G" and row["state"] == "on"
    assert row["conversation"] == AFTER_CLEAR_A


def test_이어_열기는_all_의_처음_대화_번호로(svc):
    """/clear 뒤의 새 번호로 이으면 새 목록 번호가 생기고 이름 · 옵션을 잃는다."""
    svc.live.append(bg(ID_A, AFTER_CLEAR_A, "alpha", svc.cwd, state="blocked"))  # 연결 끊김
    row = svc.entry(ID_A)
    assert row["resumeId"] == FIRST_A
    assert svc.start(row) is None
    assert names(svc)["alpha"]["state"] == "starting"
    assert svc.start(row) == "이미 처리 중입니다"
    run_background(svc)
    assert ("resume", svc.cwd, FIRST_A) in svc.calls


def test_꺼진_세션의_대화는_관리자가_기억하는_마지막_번호로(svc, monkeypatch):
    monkeypatch.setattr(agents, "last_conversation", lambda sid: AFTER_CLEAR_A)
    assert names(svc)["alpha"]["conversation"] == AFTER_CLEAR_A


def test_대화가_없는_세션은_해제_연결_이어_열기를_막는다(svc, monkeypatch):
    """관리자 기록이 계속 working 이라, 끊으면 Claude 가 다시 띄우고 그 사이 이어 열면 복사본이 생긴다."""
    monkeypatch.setattr(agents, "job_state", lambda sid: "working")
    svc.live.append(bg(ID_A, FIRST_A, "alpha", svc.cwd, pid=9, state="blocked", status="idle"))
    assert "대화가 없는" in svc.stop(svc.entry(ID_A))
    assert ("kill", 9) not in svc.calls
    assert "대화가 없는" in svc.start(svc.entry(ID_B))  # 꺼져 보이는 사이
    assert "대화가 없는" in svc.foreground(svc.entry(ID_B))
    assert not any(c[0] in ("bg", "term") for c in svc.calls)


def test_대화가_있는_세션은_관리자_기록이_done_이면_그대로_끈다(svc, monkeypatch):
    monkeypatch.setattr(agents, "job_state", lambda sid: "done")
    svc.live.append(bg(ID_A, FIRST_A, "alpha", svc.cwd, pid=9, state="done", status="idle"))
    assert svc.stop(svc.entry(ID_A)) is None
    assert ("kill", 9) in svc.calls


def test_관리자_기록_상태를_읽는다(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "JOBS", tmp_path)
    (tmp_path / ID_A).mkdir()
    (tmp_path / ID_A / "state.json").write_text('{"state": "working"}', encoding="utf-8")
    assert agents.job_state(ID_A) == "working"
    assert agents.job_state("없음") is None


def test_답을_만드는_중이면_해제를_거절한다(svc):
    svc.live.append(bg(ID_A, FIRST_A, "alpha", svc.cwd, pid=9, state="working", status="busy"))
    assert "작업 중" in svc.stop(svc.entry(ID_A))
    assert ("kill", 9) not in svc.calls


def test_백그라운드_작업만_돌면_확인을_받고_끈다(svc):
    """서버를 띄워 둔 세션은 채팅이 멈춰 있어도 busy 다."""
    svc.live.append(bg(ID_A, FIRST_A, "alpha", svc.cwd, pid=9, state="done", status="busy"))
    row = svc.entry(ID_A)
    assert "confirm" in svc.stop(row)
    assert svc.stop(row, force=True) is None
    assert ("kill", 9) in svc.calls


def test_포그라운드는_꺼졌으면_처음_번호로_이어_열고_켜졌으면_붙는다(svc):
    """켜진 대화를 포그라운드로 또 열면 복사본이 생겨 붙기만 한다."""
    assert svc.foreground(svc.entry(ID_A)) is None
    assert ("term", ["claude", "--resume", FIRST_A]) in svc.calls
    svc.live.append(bg(ID_B, FIRST_B, "beta", svc.cwd, pid=9))
    assert svc.foreground(svc.entry(ID_B)) is None
    assert ("term", ["claude", "attach", ID_B]) in svc.calls


# --- 삭제 · 복원 ---


def test_삭제하면_claude_rm_하고_복원_정보를_남긴다(svc, monkeypatch):
    removed: list[str] = []
    monkeypatch.setattr(agents, "remove", lambda sid: removed.append(sid) or None)
    monkeypatch.setattr(agents, "last_conversation", lambda sid: AFTER_CLEAR_A)
    monkeypatch.setattr(agents, "respawn_flags", lambda sid: ["--name", "alpha"])
    svc.edit(lambda d: registry.add_group(d, "G"))
    svc.place(ID_A, "G", [ID_A])
    assert svc.delete(svc.entry(ID_A)) is None
    assert names(svc)["alpha"]["state"] == "deleting"
    run_background(svc)
    assert removed == [ID_A]
    data = registry.load(svc.path)
    assert ID_A not in data["sessions"]
    record = data["deleted"][0]
    # 지우면 관리자 기록이 사라지므로 마지막 대화 번호를 먼저 읽어 둔다
    assert record["sessionId"] == AFTER_CLEAR_A and record["group"] == "G"
    assert record["cwd"] == svc.cwd and record["name"] == "alpha"
    assert record["flags"] == [
        "--name",
        "alpha",
    ]  # 지우면 관리자 기록과 함께 사라지므로 먼저 적어 둔다


def test_켜진_세션은_끄고_지운다_작업_중이면_거절(svc, monkeypatch):
    monkeypatch.setattr(agents, "remove", lambda sid: None)
    monkeypatch.setattr(agents, "wait_roster_clear", lambda c, limit=20: True)
    svc.live.append(bg(ID_A, FIRST_A, "alpha", svc.cwd, pid=9, state="working"))
    assert "작업 중" in svc.delete(svc.entry(ID_A))
    svc.live[0]["state"] = "done"
    assert svc.delete(svc.entry(ID_A)) is None
    run_background(svc)
    assert ("kill", 9) in svc.calls


def test_삭제가_실패하면_이유를_줄에_싣는다(svc, monkeypatch):
    monkeypatch.setattr(agents, "remove", lambda sid: "작업용 복사본이 있어 지우지 않았습니다")
    svc.delete(svc.entry(ID_A))
    run_background(svc)
    row = names(svc)["alpha"]
    assert "작업용 복사본" in row["error"] and row["state"] == "off"
    assert registry.load(svc.path)["deleted"] == []


def _deleted(svc, conversation=AFTER_CLEAR_A, group="G") -> None:
    def change(d):
        if group not in registry.group_names(d):
            registry.add_group(d, group)
        d["deleted"].append(
            {
                "id": ID_A,
                "name": "alpha",
                "group": group,
                "cwd": svc.cwd,
                "sessionId": conversation,
                "deletedAt": time.time(),
            }
        )

    svc.edit(change)


def test_대화_파일이_없으면_복원을_막는다(svc):
    _deleted(svc)
    assert svc.view()["deleted"][0]["restorable"] is False
    assert "정리" in svc.restore(ID_A)


def test_복원하면_마지막_대화로_띄우고_그룹을_되돌린다(svc, monkeypatch, tmp_path):
    folder = tmp_path / "claude-projects" / "C--proj"
    folder.mkdir(parents=True)
    (folder / f"{AFTER_CLEAR_A}.jsonl").write_text("{}\n", encoding="utf-8")
    _deleted(svc)
    svc.every[:] = [r for r in svc.every if r["id"] != ID_A]
    assert svc.view()["deleted"][0]["restorable"] is True

    def resumed(cwd, conv, flags, name):
        svc.calls.append(("resume", cwd, conv, tuple(flags), name))
        svc.every.append(bg("ffffffff", conv, "alpha", cwd, pid=7))

    monkeypatch.setattr(agents, "restore", resumed)
    assert svc.restore(ID_A) is None
    run_background(svc)
    assert ("resume", svc.cwd, AFTER_CLEAR_A, (), "alpha") in svc.calls
    data = registry.load(svc.path)
    assert data["deleted"] == [] and registry.group_of(data, "ffffffff") == "G"


# --- 새로 만들기 ---


def test_새_세션을_만들어_고른_그룹_끝에_넣는다(svc, monkeypatch):
    svc.edit(lambda d: registry.add_group(d, "G"))
    svc.place(ID_B, "G", [ID_B])

    def created(cwd, name):
        svc.every.append(bg("12345678", "1234-x", name, cwd, pid=5))

    monkeypatch.setattr(agents, "create", created)
    assert svc.create(svc.cwd, "gamma", "G") is None
    data = registry.load(svc.path)
    assert registry.group_of(data, "12345678") == "G"
    assert registry.order_of(data, "12345678") > registry.order_of(data, ID_B)


def test_같은_이름이_있으면_만들기_전에_묻는다(svc, monkeypatch):
    made: list[str] = []
    monkeypatch.setattr(agents, "create", lambda cwd, name: made.append(name) or "중단")
    answer = svc.create(svc.cwd, "alpha", None)
    assert "confirm" in answer and made == []
    assert svc.create(svc.cwd, "alpha", None, force=True) == "중단"
    assert made == ["alpha"]


def test_신뢰_안_된_폴더면_안내를_돌려준다(svc, monkeypatch):
    monkeypatch.setattr(
        agents, "create", lambda cwd, name: agents.explain(1, "Workspace not trusted")
    )
    assert "신뢰" in svc.create(svc.cwd, "새것", None)


def test_새_세션의_폴더와_이름을_확인한다(svc):
    assert svc.create(svc.cwd, "  ", None) == "이름을 적어 주세요"
    assert "폴더가 없습니다" in svc.create(os.path.join(svc.cwd, "없음"), "x", None)
    assert "없는 그룹" in svc.create(svc.cwd, "x", "없는 그룹")


# --- API ---


def test_API_는_세션_칸_전체를_주고_실패는_409_에_담는다(svc):
    app = FastAPI()
    app.state.claude = svc
    app.include_router(claude_routes.router)
    client = TestClient(app)

    assert client.post("/api/claude/groups", json={"name": "G"}).json() == {"ok": True}
    assert client.post("/api/claude/groups", json={"name": "G"}).status_code == 409
    body = {"group": "G", "order": [ID_B, ID_A]}
    assert client.put(f"/api/claude/sessions/{ID_A}/place", json=body).json() == {"ok": True}
    assert client.patch("/api/claude/groups/G", json={"collapsed": True}).json() == {"ok": True}

    view = client.get("/api/claude/sessions").json()
    assert view["groups"] == [{"name": "G", "collapsed": True}]
    assert [r["name"] for r in view["sessions"]] == ["beta", "alpha", "proj · cccccccc"]

    assert client.post(f"/api/claude/sessions/{ID_A}/start").json() == {"ok": True}
    r = client.post(f"/api/claude/sessions/{ID_A}/start")
    assert r.status_code == 409 and r.json() == {"error": "이미 처리 중입니다"}

    svc.live.append(bg(ID_B, FIRST_B, "beta", svc.cwd, pid=9, state="done", status="busy"))
    r = client.post(f"/api/claude/sessions/{ID_B}/stop", json={})
    assert r.status_code == 409 and "confirm" in r.json()
    assert client.post(f"/api/claude/sessions/{ID_B}/stop", json={"force": True}).json() == {
        "ok": True
    }
    assert client.post("/api/claude/sessions/nope/start").status_code == 404

    assert client.delete("/api/claude/groups/G").json() == {"ok": True}
    assert all(r["group"] is None for r in client.get("/api/claude/sessions").json()["sessions"])


def test_대화는_목록에_보이는_세션만_준다(svc):
    app = FastAPI()
    app.state.claude = svc
    app.include_router(claude_routes.router)
    client = TestClient(app)
    assert client.get(f"/api/claude/sessions/{ID_A}/chat").json() == {"messages": [], "more": False}
    assert client.get("/api/claude/sessions/99999999/chat").status_code == 404
