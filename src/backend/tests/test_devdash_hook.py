"""devdash 1겹 훅(자동 변경 기록)과 기록 저장소 테스트.

실제 ~/.devdash 를 건드리지 않도록 DEVDASH_HOME 을 임시 폴더로 바꾼다. 작업용 복사본(worktree)에서
고친 파일이 원래 저장소 기준 경로로 기록되는지 보려고 실제 git 저장소와 worktree 를 만든다.
경계 사례 상당수는 3단계 Fable 교차 검토가 재현한 것이다.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "plugins" / "devdash" / "scripts"
SKILL = SCRIPTS.parent / "skills" / "records" / "SKILL.md"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


store = _load("devdash_store")
hook = _load("devdash_hook")
tasks_mod = _load("devdash_tasks")


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """임시 DEVDASH_HOME + 커밋 하나 있는 git 저장소 + 저장소 밖 worktree 하나."""
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path / "devdash"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("a", encoding="utf-8")
    git("init", "-q", cwd=repo)
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    outside_wt = tmp_path / "wt-outside"
    git("worktree", "add", "-q", str(outside_wt), "-b", "w1", cwd=repo)
    return {"repo": repo, "root": store.norm(repo), "wt": outside_wt, "home": tmp_path / "devdash"}


def run_hook(event: str, data: dict, capsys) -> str:
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(data).encode("utf-8")))
    try:
        assert hook.main(["devdash_hook.py", event]) == 0
    finally:
        sys.stdin = sys.__stdin__
    return capsys.readouterr().out


def key_of(env) -> str:
    return store.project_key(env["root"])


def write_task(env, num: int, status: str, session: str) -> Path:
    p = store.project_dir(key_of(env)) / "tasks" / f"T-{num}-x.md"
    p.write_text(
        f"---\nstatus: {status}\nsession: {session}\ncreated: 2026-11-12\n---\n\n"
        f"# T-{num} x\n\n## 목표\n\n목표\n\n## 한 일\n\n## 남은 일\n",
        encoding="utf-8",
    )
    return p


def prompt(env, capsys, sid="s1", title="develope", **extra) -> str:
    data = {"session_id": sid, "cwd": str(env["repo"]), **extra}
    if title:
        data["session_title"] = title
    return run_hook("UserPromptSubmit", data, capsys)


def edit(env, capsys, sid: str, path: Path | None = None) -> None:
    data = {
        "session_id": sid,
        "cwd": str(env["repo"]),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path or env["repo"] / "src" / "a.py")},
    }
    run_hook("PostToolUse", data, capsys)


# --- 저장소: 키 · 등록 ---


def test_키는_경로를_폴더_이름으로_바꾸고_지문을_붙인다():
    # 없는 경로라 이 PC 의 폴더 표기에 기대지 않는다
    if sys.platform == "win32":
        key, prefix = store.project_key("c:\\__devdash_none__\\Dev"), "C--__devdash_none__-Dev-"
    else:
        key, prefix = store.project_key("/__devdash_none__/Dev"), "-__devdash_none__-Dev-"
    assert key.startswith(prefix) and len(key) == len(prefix) + 6


def test_글자만_바꾸면_같아지는_경로도_키가_다르다():
    """'C:/a b' 와 'C:/a-b' 는 지문이 없으면 같은 폴더가 된다 (Fable 재현)."""
    assert len({store.project_key(p) for p in ("C:/a b", "C:/a-b", "C:/a/b")}) == 3


@pytest.mark.skipif(sys.platform != "win32", reason="대소문자를 가리지 않는 윈도우 폴더 이름")
def test_대소문자만_다른_경로는_같은_키다(tmp_path):
    """앞부분을 입력 표기로 만들면 지문은 같은데 폴더가 둘로 갈린다 (시험이 잡은 버그)."""
    (tmp_path / "Projects" / "Dev").mkdir(parents=True)
    upper = str(tmp_path / "Projects" / "Dev")
    assert store.project_key(upper) == store.project_key(upper.lower())


def test_등록하면_폴더가_생기고_다시_등록해도_그대로다(env):
    key = store.register(env["repo"], name="내 프로젝트")
    assert (store.project_dir(key) / "tasks").is_dir()
    store.register(env["repo"], name="다른 이름")
    assert store.projects()[key]["name"] == "내 프로젝트"


# --- 저장소: 프로젝트 찾기 · 경로 ---


def test_등록_안_된_곳은_못_찾는다(env):
    assert store.find_project(str(env["repo"])) is None


def test_하위_폴더와_저장소_밖_worktree_도_원래_프로젝트로_찾는다(env):
    key = store.register(env["repo"])
    assert store.find_project(str(env["repo"] / "src")) == (key, env["root"])
    assert store.find_project(str(env["wt"])) == (key, env["root"])  # git 으로 원래 저장소를 찾음


def test_대소문자가_다른_경로도_찾고_기록한다(env):
    """'cd c:\\projects\\…' 로 시작한 세션 (Fable 재현: 찾기는 되고 기록은 빠졌다)."""
    store.register(env["repo"])
    lower = str(env["repo"]).lower()
    assert store.find_project(lower)
    assert store.relative_in_project(lower + "\\src\\a.py", env["root"]) == ("src/a.py", None)


def test_worktree_안의_파일은_원래_저장소_기준_경로로_바꾼다(env):
    store.register(env["repo"])
    assert store.relative_in_project(str(env["repo"] / "src" / "a.py"), env["root"]) == (
        "src/a.py",
        None,
    )
    assert store.relative_in_project(str(env["wt"] / "src" / "a.py"), env["root"]) == (
        "src/a.py",
        "wt-outside",
    )


def test_저장소_안_claude_worktree_도_이름을_떼어_낸다(env):
    """실제 .claude/worktrees/<이름> worktree (백그라운드 세션이 스스로 만드는 곳)."""
    store.register(env["repo"])
    inner = env["repo"] / ".claude" / "worktrees" / "bg1"
    git("worktree", "add", "-q", str(inner), "-b", "bg1", cwd=env["repo"])
    assert store.relative_in_project(str(inner / "src" / "a.py"), env["root"]) == (
        "src/a.py",
        "bg1",
    )


def test_지워진_claude_worktree_도_경로로_알아본다(env):
    path = env["repo"] / ".claude" / "worktrees" / "x" / "src" / "b.py"
    assert store.relative_in_project(str(path), env["root"]) == ("src/b.py", "x")


def test_등록_폴더가_큰_저장소의_하위_폴더여도_기록한다(env):
    """모노레포의 한 폴더만 등록한 경우 (Fable 재현: 기록이 빠졌다)."""
    (env["repo"] / "mono" / "pkg").mkdir(parents=True)
    sub_root = store.norm(env["repo"] / "mono")
    path = env["repo"] / "mono" / "pkg" / "x.py"
    assert store.relative_in_project(str(path), sub_root) == ("pkg/x.py", None)


def test_등록_폴더_안에_다른_저장소가_있어도_기록한다(env):
    nested = env["repo"] / "vendor" / "nested"
    (nested / "src").mkdir(parents=True)
    git("init", "-q", cwd=nested)
    path = nested / "src" / "a.py"
    assert store.relative_in_project(str(path), env["root"]) == ("vendor/nested/src/a.py", None)


def test_프로젝트_밖_파일은_기록하지_않는다(env, tmp_path):
    assert store.relative_in_project(str(tmp_path / "elsewhere.txt"), env["root"]) is None


# --- 저장소: 동시 기록 ---


def test_여러_프로세스가_동시에_기록해도_줄이_사라지거나_깨지지_않는다(env, tmp_path):
    """잠금 없이는 6개 프로세스 1,800줄 중 1,377줄만 남고 18줄이 깨졌다 (직접 재현)."""
    key = store.register(env["repo"])
    worker = tmp_path / "w.py"
    worker.write_text(
        "import sys\nsys.path.insert(0, sys.argv[1])\nimport devdash_store as s\n"
        "for i in range(150):\n    s.append_activity(sys.argv[2], {'w': sys.argv[3], 'i': i, 'pad': 'x' * 200})\n",
        encoding="utf-8",
    )
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(SCRIPTS), key, str(n)], env=os.environ.copy()
        )
        for n in range(6)
    ]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    lines = (store.project_dir(key) / "activity.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 900
    assert len(store.read_activity(key)) == 900


def test_읽을_때_깨진_줄은_건너뛴다(env):
    key = store.register(env["repo"])
    (store.project_dir(key) / "activity.jsonl").write_text(
        '{"a": 1}\n{"b":\n{"c": 3}\n', encoding="utf-8"
    )
    assert store.read_activity(key) == [{"a": 1}, {"c": 3}]


# --- 훅: 알림 · 이름 ---


def test_첫_차례에만_알리고_다음_차례엔_알리지_않는다(env, capsys):
    """매 차례 알리면 100차례에 수천 토큰이 쌓인다 (Fable 지적)."""
    key = store.register(env["repo"])
    first = prompt(env, capsys)
    assert "작업 기록 폴더:" in first and "세션 이름: develope" in first
    assert str(store.project_dir(key) / "tasks") in first
    assert prompt(env, capsys) == ""


def test_이름이_없으면_프로젝트_이름_순번을_붙이고_계속_쓴다(env, capsys):
    store.register(env["repo"], name="Dev")
    assert "세션 이름: Dev_001" in prompt(env, capsys, sid="a", title=None)
    assert "세션 이름: Dev_002" in prompt(env, capsys, sid="b", title=None)
    assert prompt(env, capsys, sid="a", title=None) == ""  # 같은 세션은 같은 이름, 알림 다시 없음
    assert store.session_info(key_of(env), "a")["name"] == "Dev_001"


def test_앱에서_이름을_붙이면_새_이름을_알리고_할_일_주인도_옮긴다(env, capsys):
    store.register(env["repo"], name="Dev")
    prompt(env, capsys, title=None)  # Dev_001
    doing = write_task(env, 1, "doing", "Dev_001")
    todo = write_task(env, 2, "todo", "")
    out = prompt(env, capsys, title="api-head")
    assert "세션 이름: api-head" in out
    assert tasks_mod.read_meta(doing)["session"] == "api-head"
    assert tasks_mod.read_meta(todo)["session"] == ""


def test_clear_하면_이름을_넘겨받고_진행_중_할_일도_이어진다(env, capsys):
    store.register(env["repo"], name="Dev")
    prompt(env, capsys, sid="old", title=None)  # Dev_001
    write_task(env, 1, "doing", "Dev_001")
    base = {"cwd": str(env["repo"])}
    run_hook("SessionEnd", {**base, "session_id": "old", "reason": "clear"}, capsys)
    out = run_hook("SessionStart", {**base, "session_id": "new", "source": "clear"}, capsys)
    assert "세션 이름: Dev_001" in out
    assert store.session_info(key_of(env), "new")["name"] == "Dev_001"
    assert prompt(env, capsys, sid="new", title=None) == ""  # 이미 알렸고, 새 순번을 붙이지 않는다
    edit(env, capsys, "new")
    assert store.read_activity(key_of(env))[-1]["task"] == "T-1"


@pytest.mark.parametrize("case", ["보통_종료", "다른_폴더", "시간_지남"])
def test_clear_가_아니면_이름을_넘기지_않는다(env, capsys, monkeypatch, case):
    store.register(env["repo"], name="Dev")
    prompt(env, capsys, sid="old", title=None)  # Dev_001
    end = {"cwd": str(env["repo"]), "session_id": "old", "reason": "clear"}
    start = {"cwd": str(env["repo"]), "session_id": "new", "source": "clear"}
    if case == "보통_종료":
        end["reason"] = "prompt_input_exit"
    elif case == "다른_폴더":
        start["cwd"] = str(env["repo"] / "src")
    else:
        monkeypatch.setattr(hook, "CLEAR_TTL", -1)
    run_hook("SessionEnd", end, capsys)
    assert run_hook("SessionStart", start, capsys) == ""
    assert "세션 이름: Dev_002" in prompt(env, capsys, sid="new", title=None)


@pytest.mark.parametrize("source", ["compact", "resume"])
def test_요약되거나_다시_열면_다시_알린다(env, capsys, source):
    store.register(env["repo"])
    prompt(env, capsys)
    base = {"session_id": "s1", "cwd": str(env["repo"])}
    assert "세션 이름: develope" in run_hook("SessionStart", {**base, "source": source}, capsys)
    assert run_hook("SessionStart", {**base, "source": "startup"}, capsys) == ""


def test_등록_안_된_프로젝트에서는_아무것도_안_한다(env, capsys):
    assert prompt(env, capsys) == ""
    assert not (env["home"] / "projects").exists()


def test_시작_폴더_환경_변수로도_프로젝트를_찾는다(env, capsys, monkeypatch, tmp_path):
    """백그라운드 세션의 cwd 는 복사본이지만 CLAUDE_PROJECT_DIR 은 원래 저장소다 (0006 실험)."""
    store.register(env["repo"])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(env["repo"]))
    out = run_hook(
        "UserPromptSubmit", {"session_id": "s1", "session_title": "d", "cwd": str(tmp_path)}, capsys
    )
    assert "작업 기록 폴더" in out


# --- 훅: 변경 기록 ---


def test_파일을_고치면_세션_차례_할_일과_함께_기록한다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    write_task(env, 7, "doing", "develope")
    data = {
        "session_id": "s1",
        "prompt_id": "p1",
        "cwd": str(env["wt"]),
        "tool_name": "Write",
        "tool_input": {"file_path": str(env["wt"] / "src" / "new.py")},
        "tool_response": {"type": "create"},
    }
    run_hook("PostToolUse", data, capsys)
    run_hook(
        "PostToolUse", {**data, "tool_name": "Edit", "tool_response": {}, "agent_id": "a9"}, capsys
    )
    first, second = store.read_activity(key_of(env))
    assert first | {"at": None} == {
        "at": None,
        "session": "s1",
        "prompt": "p1",
        "agent": None,
        "task": "T-7",
        "op": "create",
        "path": "src/new.py",
        "worktree": "wt-outside",
    }
    assert (second["op"], second["agent"]) == ("modify", "a9")


def test_진행_중_할_일이_없으면_할_일은_비운다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    run_hook(
        "PostToolUse",
        {
            "session_id": "s1",
            "cwd": str(env["repo"]),
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": str(env["repo"] / "n.ipynb")},
        },
        capsys,
    )
    (rec,) = store.read_activity(key_of(env))
    assert (rec["task"], rec["path"]) == (None, "n.ipynb")


def test_파일_도구가_아니거나_프로젝트_밖이면_기록하지_않는다(env, capsys, tmp_path):
    store.register(env["repo"])
    base = {"session_id": "s1", "cwd": str(env["repo"])}
    run_hook(
        "PostToolUse", {**base, "tool_name": "Bash", "tool_input": {"command": "rm x"}}, capsys
    )
    run_hook(
        "PostToolUse",
        {**base, "tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "x")}},
        capsys,
    )
    run_hook("PostToolUse", {**base, "tool_name": "Write", "tool_input": "이상한 입력"}, capsys)
    assert store.read_activity(key_of(env)) == []
    assert not (env["home"] / "hook-errors.log").exists()


# --- 훅: 마지막 작업 · 안전성 ---


def test_끝낼_때_마지막_답을_저장하고_이상한_글자도_견딘다(env, capsys):
    key = store.register(env["repo"])
    run_hook(
        "Stop",
        {"session_id": "s1", "cwd": str(env["repo"]), "last_assistant_message": "가" * 900},
        capsys,
    )
    assert len(store.session_info(key, "s1")["last_message"]) == hook.LAST_MESSAGE_LIMIT
    run_hook(
        "Stop",
        {"session_id": "s2", "cwd": str(env["repo"]), "last_assistant_message": "a\ud800b"},
        capsys,
    )
    assert store.session_info(key, "s2")["last_message"]  # 고립 대리쌍이 있어도 저장됨


def test_망가진_입력에도_세션을_방해하지_않고_오류만_남긴다(env, capsys):
    store.register(env["repo"])
    sys.stdin = io.TextIOWrapper(io.BytesIO(b"{not json"))
    try:
        assert hook.main(["devdash_hook.py", "UserPromptSubmit"]) == 0
    finally:
        sys.stdin = sys.__stdin__
    assert capsys.readouterr().out == ""
    assert "JSONDecodeError" in (env["home"] / "hook-errors.log").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh 가 없음 (윈도우는 Git Bash)")
def test_실제로_실행기로_불러도_동작한다(env):
    """훅은 실제로 'sh run.sh devdash_hook.py 이벤트' 로 불린다 (실행기가 OS 에 맞는 파이썬을 고른다).
    같은 프로세스 안 시험은 sys.path·stdin 바이트 처리를 못 본다."""
    store.register(env["repo"])
    data = json.dumps({"session_id": "s1", "session_title": "실제", "cwd": str(env["repo"])})
    r = subprocess.run(
        ["sh", str(SCRIPTS / "run.sh"), "devdash_hook.py", "UserPromptSubmit"],
        input=data.encode("utf-8"),
        capture_output=True,
        env=os.environ.copy(),
        timeout=60,
    )
    out = r.stdout.decode("utf-8")
    assert r.returncode == 0 and "세션 이름: 실제" in out and not out.lstrip().startswith("{")


def test_훅이_알리는_낱말이_규칙_문서와_같다():
    """SKILL.md 는 '작업 기록 폴더' · '세션 이름' 이라는 말로 훅의 알림을 찾는다."""
    skill = SKILL.read_text(encoding="utf-8")
    for word in ("작업 기록 폴더", "세션 이름"):
        assert word in hook.RECORDS_HINT and word in skill


# --- 3단계 Fable 검토가 짚은 빈자리 ---


def test_이름을_바꾸면_완료한_할_일도_옮기고_줄바꿈은_그대로다(env, capsys):
    store.register(env["repo"], name="Dev")
    prompt(env, capsys, title=None)  # Dev_001
    done = write_task(env, 1, "done", "Dev_001")
    before = done.read_bytes()
    # 역슬래시: 바꿀 값을 정규식 치환 문법으로 읽어 예외가 났다 (Fable 재현)
    prompt(env, capsys, title=r"fix C:\work")
    assert done.read_bytes() == before.replace(b"Dev_001", rb"fix C:\work")  # LF 가 CRLF 로 안 바뀜
    assert store.session_info(key_of(env), "s1")["name"] == r"fix C:\work"
    assert not (env["home"] / "hook-errors.log").exists()


def test_진행_중이_여럿이면_할_일을_정하지_않는다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    write_task(env, 1, "doing", "develope")
    write_task(env, 2, "doing", "develope")
    edit(env, capsys, "s1")
    assert store.read_activity(key_of(env))[-1]["task"] is None


def test_겹친_등록이면_가장_안쪽_프로젝트로_찾는다(env):
    store.register(env["repo"])
    inner = store.register(env["repo"] / "src")
    assert store.find_project(str(env["repo"] / "src" / "x"))[0] == inner


def test_시작_폴더가_작업_폴더보다_먼저다(env, capsys, monkeypatch):
    """세션을 시작한 저장소(CLAUDE_PROJECT_DIR)로 기록한다. 작업 폴더가 안쪽 등록 폴더여도."""
    outer = store.register(env["repo"])
    inner = store.register(env["repo"] / "src")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(env["repo"]))
    data = {
        "session_id": "s1",
        "cwd": str(env["repo"] / "src"),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(env["repo"] / "src" / "a.py")},
    }
    run_hook("PostToolUse", data, capsys)
    assert [r["path"] for r in store.read_activity(outer)] == ["src/a.py"]
    assert store.read_activity(inner) == []


def test_없는_경로도_대소문자를_가리지_않고_비교한다():
    """있는 경로는 realpath 가 디스크 표기로 맞춰 주지만, 지워진 폴더는 그렇지 않다."""
    assert store.same("C:/__devdash_none__/Folder") == store.same("c:/__devdash_none__/folder")


def link_dir(target: Path, link: Path) -> None:
    """폴더 링크: 윈도우는 정션(관리자 권한 없이 된다), 그 밖은 심볼릭 링크."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_정션으로_들어온_worktree_파일도_경로를_잃지_않는다(env, tmp_path):
    """git 이 준 경로 길이로 다른 표기의 경로를 자르면 빈 경로가 됐다 (Fable 재현)."""
    store.register(env["repo"])
    link = tmp_path / "j"
    link_dir(env["wt"], link)
    where = store.relative_in_project(str(link / "src" / "a.py"), env["root"])
    assert where == ("src/a.py", "wt-outside")


def test_여러_프로세스가_동시에_이름을_받아도_순번이_겹치지_않는다(env, tmp_path):
    key = store.register(env["repo"], name="Dev")
    worker = tmp_path / "n.py"
    worker.write_text(
        "import sys\nsys.path.insert(0, sys.argv[1])\nimport devdash_store as s\n"
        "print(s.next_auto_name(sys.argv[2]))\n",
        encoding="utf-8",
    )
    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(SCRIPTS), key],
            stdout=subprocess.PIPE,
            env=os.environ.copy(),
        )
        for _ in range(8)
    ]
    names = {p.communicate(timeout=60)[0].decode().strip() for p in procs}
    assert names == {f"Dev_{n:03d}" for n in range(1, 9)}


def test_변경_기록도_이상한_글자를_견딘다(env, capsys):
    store.register(env["repo"])
    # 짝 없는 서로게이트: 윈도우는 파일 이름에 올 수 있고, 리눅스는 UTF-8 이 아닌 바이트가 이렇게 풀린다
    odd = "\ud800" if sys.platform == "win32" else "\udcff"
    edit(env, capsys, f"s{odd}", env["repo"] / "src" / f"{odd}.py")
    assert len(store.read_activity(key_of(env))) == 1


def test_오류_기록은_커지면_한_번_밀어_둔다(env):
    log = env["home"] / "hook-errors.log"
    env["home"].mkdir(parents=True, exist_ok=True)
    log.write_text("x" * (hook.ERROR_LOG_LIMIT + 1), encoding="utf-8")
    try:
        raise RuntimeError("시험")
    except RuntimeError:
        hook.log_error(["hook", "Stop"])
    assert (env["home"] / "hook-errors.log.1").stat().st_size > hook.ERROR_LOG_LIMIT
    assert "RuntimeError" in log.read_text(encoding="utf-8")


# --- 같은 이름의 세션 둘 (복사본 세션 · 같은 이름을 붙인 경우) ---


def test_다른_세션이_쓰는_이름이면_갈라서_붙이고_할_일을_가져가지_않는다(env, capsys):
    """복사본 세션이 원본과 같은 이름을 받으면, 이름을 바꿀 때 원본의 할 일을 가져갔다 (Fable 재현)."""
    store.register(env["repo"])
    prompt(env, capsys, sid="A")
    t1 = write_task(env, 1, "doing", "develope")
    assert "세션 이름: develope_2" in prompt(env, capsys, sid="B")
    assert prompt(env, capsys, sid="B") == ""  # 같은 이름이 다시 와도 그대로 develope_2
    edit(env, capsys, "B")
    assert store.read_activity(key_of(env))[-1]["task"] is None  # 원본의 T-1 에 섞이지 않음
    prompt(env, capsys, sid="B", title="other")
    assert tasks_mod.read_meta(t1)["session"] == "develope"
    edit(env, capsys, "A")
    assert store.read_activity(key_of(env))[-1]["task"] == "T-1"


def test_같은_이름의_두_세션이_각자_진행_중이어도_제_할_일로_기록한다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys, sid="A")
    prompt(env, capsys, sid="B")  # develope_2
    write_task(env, 1, "doing", "develope")
    write_task(env, 2, "doing", "develope_2")
    edit(env, capsys, "A")
    edit(env, capsys, "B")
    assert [r["task"] for r in store.read_activity(key_of(env))] == ["T-1", "T-2"]


def test_clear_로_이은_세션은_같은_이름이_와도_가르지_않는다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys, sid="old")
    base = {"cwd": str(env["repo"])}
    run_hook("SessionEnd", {**base, "session_id": "old", "reason": "clear"}, capsys)
    run_hook("SessionStart", {**base, "session_id": "new", "source": "clear"}, capsys)
    assert prompt(env, capsys, sid="new") == ""
    assert store.session_info(key_of(env), "new")["name"] == "develope"


# --- 2겹 강제: 완료인데 '한 일'이 비면 끝내기를 막는다 ---


def stop(env, capsys, sid="s1", **extra) -> str | None:
    """막았으면 Claude 에게 준 이유, 아니면 None.

    decision:block 은 화면에 'Stop hook error' 로 떠서, 같은 효과에 'Stop hook feedback' 으로
    뜨는 additionalContext 를 쓴다 (실제 백그라운드 세션으로 둘 다 확인).
    """
    out = run_hook("Stop", {"session_id": sid, "cwd": str(env["repo"]), **extra}, capsys)
    if not out.strip():
        return None
    res = json.loads(out)
    assert "decision" not in res  # 'Stop hook error' 로 뜨지 않게 additionalContext 만
    assert res["hookSpecificOutput"]["hookEventName"] == "Stop"
    return res["hookSpecificOutput"]["additionalContext"]


def test_완료인데_한_일이_비면_끝내기를_막고_풀_방법을_알린다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    t = write_task(env, 3, "done", "develope")
    reason = stop(env, capsys)
    assert str(t) in reason and "## 한 일" in reason and "doing" in reason


def test_한_번_막은_뒤에는_다시_막지_않는다(env, capsys):
    """채우지 못하는 상황에서 세션이 끝없이 붙잡히지 않게 (stop_hook_active)."""
    store.register(env["repo"])
    prompt(env, capsys)
    write_task(env, 3, "done", "develope")
    assert stop(env, capsys, stop_hook_active=True) is None


def test_한_일을_채웠거나_다른_세션_것이거나_진행_중이면_막지_않는다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    filled = write_task(env, 1, "done", "develope")
    filled.write_text(
        filled.read_text(encoding="utf-8").replace(
            "## 한 일\n", "## 한 일\n\n- 2026-11-12 확인 창 추가 — 실수 방지\n"
        ),
        encoding="utf-8",
    )
    write_task(env, 2, "done", "other")
    write_task(env, 3, "doing", "develope")
    assert stop(env, capsys) is None


def test_안내_주석만_있거나_항목이_없어도_빈_것으로_본다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    t = write_task(env, 1, "done", "develope")
    text = t.read_text(encoding="utf-8")
    t.write_text(text.replace("## 한 일\n", "## 한 일\n\n<!-- 무엇을 했나 -->\n-\n"), "utf-8")
    assert "한 일" in stop(env, capsys)
    t.write_text(text.replace("## 한 일\n", ""), encoding="utf-8")
    assert "없는 항목" in stop(env, capsys)  # 왜 비어 보이는지 형식 검사의 이유를 붙인다


def test_이름_없는_세션이나_등록_안_된_프로젝트에서는_막지_않는다(env, capsys):
    assert stop(env, capsys) is None  # 등록 전
    store.register(env["repo"])
    write_task(env, 1, "done", "")
    assert stop(env, capsys, sid="never-prompted") is None


# --- 2겹 알림: 차례의 첫 파일 변경 때 한 번 ---


def edit_out(env, capsys, sid="s1", prompt_id="p1", **extra) -> str | None:
    data = {
        "session_id": sid,
        "prompt_id": prompt_id,
        "cwd": str(env["repo"]),
        "tool_name": "Edit",
        "tool_input": {"file_path": str(env["repo"] / "src" / "a.py")},
        **extra,
    }
    out = run_hook("PostToolUse", data, capsys)
    if not out.strip():
        return None
    ctx = json.loads(out)["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse"
    return ctx["additionalContext"]


def test_차례의_첫_변경_때만_진행_중_할_일에_적으라고_알린다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    write_task(env, 3, "doing", "develope")
    assert "T-3" in edit_out(env, capsys) and "## 한 일" in edit_out(env, capsys, prompt_id="p0")
    assert edit_out(env, capsys, prompt_id="p0") is None  # 같은 차례 두 번째 변경
    assert "T-3" in edit_out(env, capsys, prompt_id="p2")  # 다음 차례


def test_진행_중_할_일이_없거나_여럿이면_그렇다고_알린다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    assert "어느 할 일에도 붙지 않습니다" in edit_out(env, capsys, prompt_id="p1")
    write_task(env, 2, "doing", "develope")
    write_task(env, 10, "doing", "develope")
    assert "T-2, T-10" in edit_out(env, capsys, prompt_id="p2")  # 번호 순


def test_서브에이전트나_이름_없는_세션에는_알리지_않는다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    assert edit_out(env, capsys, agent_id="a1") is None
    assert edit_out(env, capsys, sid="unnamed") is None  # 알림(기록 규칙)을 받기 전
    assert edit_out(env, capsys) is not None  # 서브에이전트가 먼저 고쳐도 부모에게는 알린다


# --- 4단계 Fable 검토가 짚은 것 ---


def test_이번_차례_전에_완료된_옛_할_일로는_막지_않는다(env, capsys):
    """사람이 손으로 완료한 옛 할 일 하나가 매 차례 끝내기를 막았다 (Fable 재현)."""
    store.register(env["repo"])
    old = write_task(env, 1, "done", "develope")
    past = time.time() - 3600
    os.utime(old, (past, past))
    prompt(env, capsys)
    assert stop(env, capsys) is None


def test_차례_시작_시각이_없으면_막지_않는다(env, capsys):
    store.register(env["repo"])
    write_task(env, 1, "done", "develope")
    store.update_session(key_of(env), "s1", name="develope")  # 차례를 거치지 않은 세션
    assert stop(env, capsys) is None


def test_따옴표로_감싼_세션_이름도_같은_이름이다(env, capsys):
    """따옴표를 안 떼면 진행 중 할 일이 없다고 잘못 알리고 강제도 빠졌다 (Fable 재현)."""
    store.register(env["repo"])
    prompt(env, capsys)
    t = write_task(env, 1, "doing", '"develope"')
    assert "T-1" in edit_out(env, capsys)
    t.write_text(t.read_text(encoding="utf-8").replace("status: doing", "status: done"), "utf-8")
    assert "T-1" in stop(env, capsys)


def test_이름이_형식에_안_맞거나_UTF8_이_아닌_할_일은_건너뛴다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    d = store.project_dir(key_of(env)) / "tasks"
    (d / "T-03-x.md").write_text(write_task(env, 9, "done", "develope").read_text("utf-8"), "utf-8")
    (d / "T-9-x.md").write_bytes(
        "---\nstatus: done\nsession: develope\n---\n# 한글\n".encode("cp949")
    )
    assert stop(env, capsys) is None


def test_세션_번호나_차례_번호가_없으면_조용히_넘어간다(env, capsys):
    store.register(env["repo"])
    prompt(env, capsys)
    assert run_hook("Stop", {"cwd": str(env["repo"])}, capsys) == ""
    assert edit_out(env, capsys, prompt_id=None) is None


def test_한_응답의_변경_여럿이_동시에_와도_알림은_한_번이다(env):
    """PostToolUse 는 병렬로 돈다. 잠금 없이는 10번 중 4번 두 번 알렸다 (Fable 재현)."""
    store.register(env["repo"])
    store.update_session(key_of(env), "s1", name="develope")
    data = json.dumps(
        {
            "session_id": "s1",
            "prompt_id": "p1",
            "cwd": str(env["repo"]),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(env["repo"] / "src" / "a.py")},
        }
    ).encode("utf-8")
    procs = [
        subprocess.Popen(
            [sys.executable, str(SCRIPTS / "devdash_hook.py"), "PostToolUse"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=os.environ.copy(),
        )
        for _ in range(6)
    ]
    outs = [p.communicate(data, timeout=60)[0] for p in procs]
    assert sum(1 for o in outs if o.strip()) == 1
    assert len(store.read_activity(key_of(env))) == 6


# --- 등록 명령 (/devdash:init 이 부른다) ---


def test_작업용_복사본이나_하위_폴더에서_등록해도_원래_저장소를_등록한다(env):
    """하위 폴더를 등록하면 복사본에서 일하는 세션이 프로젝트를 못 찾았다 (5단계 Fable 재현)."""
    inner = env["repo"] / ".claude" / "worktrees" / "bg1"
    git("worktree", "add", "-q", str(inner), "-b", "bg1", cwd=env["repo"])
    for where in (inner, inner / "src", env["wt"], env["repo"] / "src", env["repo"]):
        assert store.same(store.main_path(str(where))) == store.same(env["repo"]), where
    store.register(store.main_path(str(inner / "src")))
    assert store.find_project(str(inner / "src"))  # 복사본 세션도 찾는다


def test_git_이_아닌_폴더는_그_폴더를_등록한다(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert store.same(store.main_path(str(plain))) == store.same(plain)


def test_등록_명령은_처음과_이미_등록을_가르고_빈_이름이면_폴더_이름을_쓴다(env, capsys):
    # 스킬은 이름이 없어도 '' 를 넘긴다: register . ""
    assert store.main(["devdash_store.py", "register", str(env["wt"]), ""]) == 0
    out = capsys.readouterr().out
    assert "등록했습니다" in out and "(이름: repo)" in out and "작업 기록 폴더:" in out
    assert store.main(["devdash_store.py", "register", str(env["repo"]), "새 이름"]) == 0
    out = capsys.readouterr().out
    assert "이미 등록되어 있습니다" in out and "(이름: repo," in out
    assert store.main(["devdash_store.py"]) == 2


def test_등록하지_못해도_종료_코드는_0_이고_이유를_알린다(env, capsys, monkeypatch):
    """0 이 아니면 스킬 전체가 멈춰 이유가 사용자에게 안 간다."""

    def refuse(*_a, **_k):
        raise ValueError("같은 키를 다른 경로가 쓰고 있습니다")

    monkeypatch.setattr(store, "register", refuse)
    assert store.main(["devdash_store.py", "register", str(env["repo"])]) == 0
    assert "등록하지 못했습니다: 같은 키" in capsys.readouterr().out


def test_플러그인_훅_등록은_훅_스크립트가_처리하는_이벤트와_맞는다():
    hooks = json.loads((SCRIPTS.parent / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    events = set(hooks["hooks"])
    assert events == {
        "UserPromptSubmit",
        "SessionStart",
        "SessionEnd",
        "PreToolUse",
        "PostToolUse",
        "Stop",
    }
    for event, groups in hooks["hooks"].items():
        command = groups[0]["hooks"][0]["command"]
        assert command == f'sh "${{CLAUDE_PLUGIN_ROOT}}/scripts/run.sh" devdash_hook.py {event}'
    pre = hooks["hooks"]["PreToolUse"][0]["matcher"].split("|")
    assert set(pre) == {*hook.FILE_TOOLS, *hook.SHELL_TOOLS}  # 셸 명령 직전 git 상태
    # 셸 명령이 바꾼 파일도 스냅샷으로 남긴다 (직전 내용은 바뀐 부분을 거꾸로 적용해 만든다)
    post = hooks["hooks"]["PostToolUse"][0]["matcher"].split("|")
    assert set(post) == {*hook.FILE_TOOLS, *hook.SHELL_TOOLS}


# --- 화면 세션 칸의 "마지막 작업 · 언제" (6단계) ---


def test_끝낼_때_마지막_작업의_시각을_남기고_clear_하면_함께_넘긴다(env, capsys):
    key = store.register(env["repo"])
    prompt(env, capsys, sid="old")
    run_hook(
        "Stop",
        {"session_id": "old", "cwd": str(env["repo"]), "last_assistant_message": "끝"},
        capsys,
    )
    old = store.session_info(key, "old")
    assert old["last_message"] == "끝" and old["last_at"]
    base = {"cwd": str(env["repo"])}
    run_hook("SessionEnd", {**base, "session_id": "old", "reason": "clear"}, capsys)
    run_hook("SessionStart", {**base, "session_id": "new", "source": "clear"}, capsys)
    new = store.session_info(key, "new")
    assert (new["last_message"], new["last_at"]) == ("끝", old["last_at"])


# --- 스냅샷: 작업 · 차례별 바뀐 파일 비교 ---

snap = _load("devdash_snap")


def tool(env, capsys, event, path, prompt_id="p1", name="Edit", response=None, sid="s1"):
    data = {
        "session_id": sid,
        "prompt_id": prompt_id,
        "cwd": str(env["repo"]),
        "tool_name": name,
        "tool_input": {"file_path": str(path)},
    }
    if response is not None:
        data["tool_response"] = response
    run_hook(event, data, capsys)


def edit_file(env, capsys, path: Path, text: str, prompt_id="p1", create=False) -> None:
    """Claude 의 Edit 한 번: 직전 훅 → 파일 고침 → 직후 훅."""
    tool(env, capsys, "PreToolUse", path, prompt_id, "Write" if create else "Edit")
    path.write_text(text, encoding="utf-8")
    response = {"type": "create"} if create else {}
    tool(env, capsys, "PostToolUse", path, prompt_id, "Write" if create else "Edit", response)


def snaps(env) -> list[dict]:
    return snap.entries(store.project_dir(key_of(env)))


def content(env, blob) -> str:
    return snap.get(store.project_dir(key_of(env)), blob).decode("utf-8")


@pytest.fixture
def doing(env, capsys):
    """등록된 프로젝트 + 진행 중 할 일 T-5 하나를 맡은 세션 s1."""
    store.register(env["repo"])
    prompt(env, capsys)
    write_task(env, 5, "doing", "develope")


def test_고치기_직전과_직후를_남기고_비교는_첫_직전에서_마지막_직후로(env, capsys, doing):
    """커밋 안 된 변경이 있던 파일도 그 작업이 고치기 직전 내용이 '이전' 이다 (커밋을 보지 않는다)."""
    a = env["repo"] / "src" / "a.py"
    a.write_text("커밋 안 된 변경", encoding="utf-8")
    edit_file(env, capsys, a, "첫 수정")
    edit_file(env, capsys, a, "두 번째 수정")  # 같은 차례: 직전은 다시 남기지 않는다
    edit_file(env, capsys, a, "다음 차례", prompt_id="p2")
    rows = snaps(env)
    assert [(r["when"], r["prompt"]) for r in rows] == [
        ("before", "p1"),
        ("after", "p1"),
        ("after", "p1"),
        ("before", "p2"),
        ("after", "p2"),
    ]
    assert all(r["task"] == "T-5" and r["path"] == "src/a.py" for r in rows)
    before, after = snap.pair(rows, lambda r: r["task"] == "T-5" and r["path"] == "src/a.py")
    assert (content(env, before), content(env, after)) == ("커밋 안 된 변경", "다음 차례")
    turn = snap.pair(rows, lambda r: r["prompt"] == "p1")
    assert (content(env, turn[0]), content(env, turn[1])) == ("커밋 안 된 변경", "두 번째 수정")


def test_새로_만든_파일은_이전이_없다(env, capsys, doing):
    new = env["repo"] / "src" / "new.py"
    edit_file(env, capsys, new, "새 파일", create=True)
    before, after = snap.pair(snaps(env), lambda r: r["path"] == "src/new.py")
    assert before is None and content(env, after) == "새 파일"


def test_같은_내용은_다시_넣어도_저장소가_늘지_않는다(env, capsys, doing):
    a = env["repo"] / "src" / "a.py"
    edit_file(env, capsys, a, "같은 내용")
    project = store.project_dir(key_of(env))

    def stored() -> str:
        # git 에게 묻는다 — 시험 경로가 길어 파이썬이 폴더를 훑지 못한다
        return snap._git(project, "count-objects", "-v").decode()

    count = stored()
    for i in range(3):
        edit_file(env, capsys, a, "같은 내용", prompt_id=f"p{i + 2}")
    assert stored() == count


def test_git_이_아닌_폴더에서도_남긴다(env, capsys, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "memo.md").write_text("처음", encoding="utf-8")
    store.register(plain)
    data = {"session_id": "s1", "cwd": str(plain), "session_title": "develope"}
    run_hook("UserPromptSubmit", data, capsys)
    for event, text in (("PreToolUse", None), ("PostToolUse", "고침")):
        if text:
            (plain / "memo.md").write_text(text, encoding="utf-8")
        payload = {
            **data,
            "prompt_id": "p1",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(plain / "memo.md")},
        }
        run_hook(event, payload, capsys)
    project = store.project_dir(store.project_key(plain))
    before, after = snap.pair(snap.entries(project), lambda r: r["path"] == "memo.md")
    assert (
        snap.get(project, before) == "처음".encode() and snap.get(project, after) == "고침".encode()
    )


def shell(
    cwd: Path,
    capsys,
    command: str,
    change=None,
    prompt_id="p1",
    use="t1",
    response=None,
    name="Bash",
):
    """Claude 의 셸 명령 한 번: 직전 훅 → 명령(change) → 직후 훅."""
    data = {
        "session_id": "s1",
        "prompt_id": prompt_id,
        "cwd": str(cwd),
        "tool_name": name,
        "tool_use_id": use,
        "tool_input": {"command": command},
    }
    run_hook("PreToolUse", data, capsys)
    if change:
        change()
    run_hook("PostToolUse", {**data, "tool_response": response or {"stdout": ""}}, capsys)


def acts(env) -> list[dict]:
    return store.read_activity(key_of(env))


def test_셸로_고친_파일은_명령_전후_git_상태로_잡고_직전은_마지막_커밋(env, capsys, doing):
    """명령 전에 깨끗했던 파일 · 새로 만든 파일 · 지운 파일. bashEditDiff 가 없어도 된다."""
    repo = env["repo"]
    (repo / "src" / "b.txt").write_text("사과\n", encoding="utf-8")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "b", cwd=repo)

    def change():
        (repo / "src" / "a.py").write_text("a 고침", encoding="utf-8")
        (repo / "src" / "new.txt").write_text("새", encoding="utf-8")
        (repo / "src" / "b.txt").unlink()

    shell(repo, capsys, "python fix.py", change)
    rows = {(r["path"], r["when"]): r for r in snaps(env)}
    assert content(env, rows["src/a.py", "before"]["blob"]) == "a"  # HEAD
    assert content(env, rows["src/a.py", "after"]["blob"]) == "a 고침"
    assert rows["src/new.txt", "before"]["blob"] is None
    assert content(env, rows["src/b.txt", "before"]["blob"]) == "사과\n"
    assert rows["src/b.txt", "after"]["blob"] is None
    ops = {a["path"]: (a["op"], a["tool"], a["task"]) for a in acts(env)}
    assert ops == {
        "src/a.py": ("modify", "Bash", "T-5"),
        "src/new.txt": ("create", "Bash", "T-5"),
        "src/b.txt": ("delete", "Bash", "T-5"),
    }


def test_이미_고쳐져_있던_파일은_명령_직전에_떠_둔_내용이_직전이다(env, capsys, doing):
    repo = env["repo"]
    a = repo / "src" / "a.py"
    a.write_text("커밋 안 된 변경", encoding="utf-8")
    (repo / "src" / "other.txt").write_text(
        "안 바뀜", encoding="utf-8"
    )  # 고쳐져 있지만 명령이 안 건드림
    shell(
        repo,
        capsys,
        "sed -i s/변경/수정/ src/a.py",
        lambda: a.write_text("커밋 안 된 수정", encoding="utf-8"),
    )
    rows = snaps(env)
    assert [(r["path"], r["when"]) for r in rows] == [("src/a.py", "before"), ("src/a.py", "after")]
    assert content(env, rows[0]["blob"]) == "커밋 안 된 변경"
    # 다음 명령: 그대로인 고쳐진 파일은 다시 넣지 않는다 (시각 · 크기가 같으면 떠 둔 번호를 쓴다)
    calls = []
    real = snap.put_paths
    snap.put_paths = lambda d, paths: calls.append(paths) or real(d, paths)
    try:
        shell(repo, capsys, "python noop.py", use="t2")
    finally:
        snap.put_paths = real
    assert calls == [[]] or calls == []
    assert len(snaps(env)) == 2


def test_읽기만_하는_셸_명령은_git_도_부르지_않는다(env, capsys, doing, monkeypatch):
    called = []
    monkeypatch.setattr(snap, "status", lambda top: called.append(top) or {})
    monkeypatch.setattr(hook, "_snap", lambda: snap)
    for command in (
        "ls -la src",
        "cat a.py | head -3",
        "git log --oneline -5 && git status",
        "cd src && grep -n x a.py 2>/dev/null",
    ):
        shell(env["repo"], capsys, command)
    assert called == []
    shell(env["repo"], capsys, "sed -i s/a/b/ src/a.py")  # 애매하면 돌린다
    assert len(called) == 2  # 직전 · 직후


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls", True),
        ("git -C x diff --stat", True),
        ("FOO=1 rg pattern src | sort | uniq -c", True),
        ("echo hi > out.txt", False),
        ("echo hi 2>&1", True),
        ("cat a > /dev/null", True),
        ("sed -n 1,5p a.py", False),
        ("find . -name '*.pyc' -delete", False),
        ("sort -o out.txt in.txt", False),
        # S3 검토: 읽기 전용 목록이지만 인자에 따라 파일을 쓰는 경우
        ("sort -uo out.txt in.txt", False),
        ("sort -u in.txt", True),
        ("uniq in.txt out.txt", False),
        ("sort a | uniq -c", True),
        ("uniq -c in.txt", True),
        ("xxd -r dump.hex out.bin", False),
        ("xxd a.bin", False),  # xxd 는 아예 목록에 없다 (출력 파일 인자)
        ("tree -o out.txt", False),
        ("tree -L 2 src", True),
        ("find . -fprint0 out", False),
        ("find . -fprintf out %p", False),
        ("find . -fls out", False),
        ("find . -name '*.py'", True),
        ("git diff --output=patch.txt", False),
        ("git log --output=log.txt -3", False),
        ("git -C x log --oneline -3", True),
        ("less -o log.txt a", False),
        ("fd -x rm", False),
        ("file -C -m magic", False),
        ("git commit -m x", False),
        ("git branch -D x", False),
        ("ls $(pwd)", False),
        ("python script.py", False),
        ("npm test", False),
        ("", False),
    ],
)
def test_읽기만_하는_명령_가리기(command, expected):
    assert snap.read_only(command) is expected


def test_셸이_건드렸지만_내용이_그대로면_남기지_않는다(env, capsys, doing):
    repo = env["repo"]
    a = repo / "src" / "a.py"
    shell(repo, capsys, "touch src/a.py", lambda: a.write_text("a", encoding="utf-8"))
    assert snaps(env) == [] and acts(env) == []


@pytest.fixture
def plain(tmp_path, capsys, env):
    """git 이 아닌 등록 폴더 + 진행 중 할 일 T-5 (세션 s1)."""
    folder = tmp_path / "plain"
    folder.mkdir()
    store.register(folder)
    run_hook(
        "UserPromptSubmit",
        {"session_id": "s1", "cwd": str(folder), "session_title": "develope"},
        capsys,
    )
    key = store.project_key(store.norm(folder))
    (store.project_dir(key) / "tasks" / "T-5-x.md").write_text(
        "---\nstatus: doing\nsession: develope\ncreated: 2026-11-12\n---\n\n# T-5 x\n",
        encoding="utf-8",
    )
    return {"dir": folder, "key": key}


def bash_diff(path: Path, hunks: list[dict]) -> dict:
    return {"stdout": "", "bashEditDiff": {"files": [{"filePath": str(path), "hunks": hunks}]}}


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_git_이_아닌_폴더는_bashEditDiff_를_거꾸로_적용해_직전_내용을_만든다(capsys, plain, eol):
    """바뀐 부분의 줄에는 줄 끝이 없다 — 윈도우 줄바꿈(CRLF) 파일도 그 줄바꿈 그대로 되돌린다."""
    a = plain["dir"] / "a.py"
    a.write_bytes("하나\n둘째\n셋\n".replace("\n", eol).encode())  # sed 가 바꾼 뒤
    hunks = [
        {
            "oldStart": 1,
            "oldLines": 3,
            "newStart": 1,
            "newLines": 3,
            "lines": [" 하나", "-둘", "+둘째", " 셋"],
        }
    ]
    shell(plain["dir"], capsys, "sed -i s/둘/둘째/ a.py", response=bash_diff(a, hunks))
    project = store.project_dir(plain["key"])
    rows = snap.entries(project)
    assert [(r["when"], r["tool"]) for r in rows] == [("before", "Bash"), ("after", "Bash")]
    assert snap.get(project, rows[0]["blob"]).decode() == "하나\n둘\n셋\n".replace("\n", eol)
    act = store.read_activity(plain["key"])
    assert act[-1]["path"] == "a.py" and act[-1]["tool"] == "Bash" and act[-1]["task"] == "T-5"


def test_git_이_아닌_폴더에서_바뀐_부분이_맞지_않으면_직전은_알_수_없음(capsys, plain):
    a = plain["dir"] / "a.py"
    a.write_text("전혀 다른 내용\n", encoding="utf-8")
    hunks = [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1, "lines": ["-x", "+y"]}]
    shell(plain["dir"], capsys, "sed -i s/x/y/ a.py", response=bash_diff(a, hunks))
    assert snap.entries(store.project_dir(plain["key"]))[0]["blob"] == "unknown"


def test_git_이_아닌_폴더에서_bashEditDiff_가_없으면_못_잡는다(capsys, plain):
    a = plain["dir"] / "a.py"
    shell(plain["dir"], capsys, "python fix.py", lambda: a.write_text("새", encoding="utf-8"))
    assert snap.entries(store.project_dir(plain["key"])) == []


def test_스냅샷이_실패해도_변경_기록과_알림은_한다(env, capsys, doing, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("git 없음")

    monkeypatch.setattr(snap, "put", broken)
    monkeypatch.setattr(hook, "_snap", lambda: snap)
    a = env["repo"] / "src" / "a.py"
    tool(env, capsys, "PostToolUse", a, prompt_id="p9")
    assert store.read_activity(key_of(env))[-1]["prompt"] == "p9"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("Get-ChildItem src", True),
        ("Get-Content a.py -TotalCount 5 | Select-String x", True),
        ("Test-Path a.py; git status", True),
        ("Get-ChildItem 2>$null", True),
        ("gci -Recurse | Measure-Object", True),
        ("Get-Content a.py | Set-Content b.py", False),
        ("Get-Content a.py > b.py", False),
        ("'x' | Out-File a.txt -Encoding utf8", False),
        ("Get-ChildItem | ForEach-Object { Remove-Item $_ }", False),
        ("$x = Get-Content a.py", False),
        ("& git merge s3", False),
        ("git merge --ff-only s3", False),
        ("git log --output=log.txt", False),
        ("Remove-Item a.py", False),
        ("", False),
    ],
)
def test_PowerShell_읽기만_하는_명령_가리기(command, expected):
    """확실한 몇 개만 건너뛴다 — 애매하면 돌린다."""
    assert snap.read_only_ps(command) is expected


def test_PowerShell_로_고친_파일도_명령_전후_git_상태로_잡는다(env, capsys, doing):
    """S3 검토: 매처에 PowerShell 이 없어 이 PC 셸 호출의 약 11% 가 빠졌다."""
    repo = env["repo"]
    a = repo / "src" / "a.py"

    def change():
        a.write_text("a 고침", encoding="utf-8")

    shell(repo, capsys, "Set-Content src/a.py 'a 고침' -Encoding utf8", change, name="PowerShell")
    rows = snaps(env)
    assert [(r["path"], r["when"], r["tool"]) for r in rows] == [
        ("src/a.py", "before", "PowerShell"),
        ("src/a.py", "after", "PowerShell"),
    ]
    assert content(env, rows[0]["blob"]) == "a"
    assert acts(env)[-1]["tool"] == "PowerShell" and acts(env)[-1]["task"] == "T-5"


def test_PowerShell_읽기_명령은_git_도_부르지_않는다(env, capsys, doing, monkeypatch):
    called = []
    monkeypatch.setattr(snap, "status", lambda top: called.append(top) or {})
    monkeypatch.setattr(hook, "_snap", lambda: snap)
    shell(env["repo"], capsys, "Get-ChildItem src | Select-Object Name", name="PowerShell")
    assert called == []
    shell(env["repo"], capsys, "Copy-Item a b", name="PowerShell")
    assert len(called) == 2
