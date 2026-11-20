"""프로젝트 탭 API 테스트.

화면이 칸마다 하나씩 부르는 계약이다. 실제 ~/.devdash(세션 배치 포함)는 건드리지 않게
DEVDASH_HOME 과 세션 배치 경로를 임시 폴더로 바꾼다. git 이력 · 파일 목록은 실제 git 저장소로 본다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devdash import local, records
from devdash.api.project_routes import router
from devdash.claude import registry

store = records.store


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class FakeSessions:
    """라우트가 쓰는 것은 세션 배치 경로와 세션 칸의 줄(rows) 뿐이다.
    줄의 사람이 정한 프로젝트는 진짜 서비스처럼 배치 파일에서 읽는다."""

    def __init__(self, path: Path, base: list[dict]):
        self.path = path
        self.base = base

    def rows(self) -> list[dict]:
        data = registry.load(self.path)
        return [{**r, "project": registry.project_of(data, r["id"])} for r in self.base]


def row(sid, name, cwd, group="G", state="off", activity=None, conversation=None) -> dict:
    return {
        "id": sid,
        "name": name,
        "group": group,
        "cwd": str(cwd),
        "state": state,
        "activity": activity,
        "conversation": conversation or sid,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path / "devdash"))
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("a", encoding="utf-8")
    (repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    git("init", "-q", cwd=repo)
    git("add", ".", cwd=repo)
    git("commit", "-q", "-m", "처음 [T-1]", cwd=repo)
    other = tmp_path / "other"
    other.mkdir()
    sessions = tmp_path / "sessions.json"
    base = [
        row("s-1", "develope", repo, state="on", activity="working"),
        row("s-2", "other-head", other),
        row("s-3", "gone", tmp_path / "없음", group="H"),
    ]
    app = FastAPI()
    app.state.claude = FakeSessions(sessions, base)
    app.include_router(router)
    return {
        "repo": repo,
        "other": other,
        "sessions": sessions,
        "base": base,
        "app": app,
        "tmp": tmp_path,
    }


@pytest.fixture
def client(env):
    return TestClient(env["app"])


def add(client, env, name=None) -> str:
    r = client.post("/api/projects", json={"root": str(env["repo"]), "name": name})
    assert r.status_code == 200, r.text
    return r.json()["key"]


def write_task(key: str, num: int, status: str, session: str, done: str = "") -> Path:
    p = store.project_dir(key) / "tasks" / f"T-{num}-x.md"
    p.write_text(
        f"---\nstatus: {status}\nsession: {session}\ncreated: 2026-11-12\n---\n\n"
        f"# T-{num} 할 일 {num}\n\n## 목표\n\n목표\n\n## 한 일\n\n{done}\n\n## 남은 일\n",
        encoding="utf-8",
    )
    return p


# --- 목록 · 추가 ---


def test_후보는_세션_목록의_폴더_중_등록_안_된_것이다(client, env):
    names = [c["name"] for c in client.get("/api/projects/candidates").json()]
    assert names == ["other", "repo"]  # 없는 폴더(s-3)는 빠진다
    add(client, env)
    cand = client.get("/api/projects/candidates").json()
    assert [c["name"] for c in cand] == ["other"] and cand[0]["sessions"] == ["other-head"]


def test_등록하면_목록에_개수와_함께_나온다(client, env):
    key = add(client, env, "내 프로젝트")
    write_task(key, 1, "doing", "develope")
    write_task(key, 2, "todo", "")
    (row,) = client.get("/api/projects").json()
    assert row["name"] == "내 프로젝트" and row["root"].endswith("repo")
    assert row["doing"] == 1


def test_없는_폴더는_등록하지_않는다(client, env):
    r = client.post("/api/projects", json={"root": str(env["tmp"] / "없음")})
    assert r.status_code == 409 and "폴더가 없습니다" in r.json()["error"]


def test_폴더_선택_창의_결과를_돌려준다(client, monkeypatch):
    monkeypatch.setattr(local, "pick_folder", lambda: "C:/Projects/X")
    assert client.post("/api/projects/pick-folder").json() == {"path": "C:/Projects/X"}
    monkeypatch.setattr(local, "pick_folder", lambda: None)
    assert client.post("/api/projects/pick-folder").json() == {"path": None}


def test_모르는_프로젝트는_404(client):
    for url in ("files", "sessions", "session-choices", "tasks", "tasks/1", "git"):
        assert client.get(f"/api/projects/없음/{url}").status_code == 404


# --- 가운데: 할 일 · 정리 문서 ---


def test_할_일_목록은_번호순이고_형식_문제를_함께_준다(client, env):
    key = add(client, env)
    write_task(key, 10, "doing", "develope")
    write_task(key, 2, "done", "develope")  # 한 일이 비었다
    rows = client.get(f"/api/projects/{key}/tasks").json()
    assert [r["number"] for r in rows] == [2, 10]
    assert rows[1]["title"] == "할 일 10" and rows[1]["problems"] == []
    assert any("한 일" in p for p in rows[0]["problems"])


def test_정리_문서는_항목과_이_작업에서_바뀐_파일을_준다(client, env):
    key = add(client, env)
    write_task(key, 3, "doing", "develope", done="- 2026-11-12 확인 창 추가")
    for op, path, task in [
        ("create", "src/b.py", "T-3"),
        ("modify", "src/b.py", "T-3"),
        ("modify", "src/a.py", "T-3"),
        ("modify", "src/c.py", "T-4"),
    ]:
        store.append_activity(
            key, {"at": "2026-11-12T10:00:00", "op": op, "path": path, "task": task}
        )
    d = client.get(f"/api/projects/{key}/tasks/3").json()
    assert d["sections"]["한 일"] == "- 2026-11-12 확인 창 추가" and d["status"] == "doing"
    assert [(f["path"], f["created"], f["changes"]) for f in d["files"]] == [
        ("src/a.py", False, 1),
        ("src/b.py", True, 2),
    ]
    assert client.get(f"/api/projects/{key}/tasks/99").status_code == 404


# --- 왼쪽: 파일 · 세션 ---


def test_파일은_추적_중과_새_파일과_git_제외_파일을_보인다(client, env):
    key = add(client, env)
    (env["repo"] / "src" / "new.py").write_text("n", encoding="utf-8")
    (env["repo"] / "secret.txt").write_text("s", encoding="utf-8")
    (env["repo"] / "한글 파일.md").write_text("h", encoding="utf-8")
    store.append_activity(key, {"op": "create", "path": "src/new.py", "task": "T-2"})
    store.append_activity(key, {"op": "modify", "path": "src/new.py", "task": "T-5"})
    store.append_activity(key, {"op": "modify", "path": "src/new.py", "task": None})
    out = client.get(f"/api/projects/{key}/files").json()
    rows = {f["path"]: f for f in out["files"]}
    assert ".gitignore" in rows and "src/a.py" in rows and "한글 파일.md" in rows
    # 이 PC 의 작업 폴더를 보므로 git 에 올리지 않는 파일도 보인다 — 흐리게 보이게 표시만
    assert rows["secret.txt"]["ignored"] is True and "ignored" not in rows["src/a.py"]
    new = rows["src/new.py"]
    assert (new["createdTask"], new["lastTask"], new["changes"]) == ("T-2", "T-5", 3)
    assert "changes" not in rows["src/a.py"]


def test_git_제외_폴더_중_의존성_캐시_다른_저장소는_들어가지_않는다(client, env):
    repo = env["repo"]
    (repo / ".gitignore").write_text(
        "*.env\nlogs/\nnode_modules/\n.claude/\npkg.egg-info/\n", "utf-8"
    )
    for path in (
        "a.env",
        "logs/app.log",
        "logs/old/1.log",
        "logs/__pycache__/x.pyc",  # 제외 폴더 안의 캐시
        "node_modules/x/i.js",
        "pkg.egg-info/PKG-INFO",
        ".claude/settings.local.json",
        ".claude/worktrees/bg/src/a.py",  # 작업용 복사본 (안에 .git 이 있다)
    ):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("x", encoding="utf-8")
    (repo / ".claude" / "worktrees" / "bg" / ".git").write_text("gitdir: x", encoding="utf-8")
    key = add(client, env)
    ignored = {
        f["path"]
        for f in client.get(f"/api/projects/{key}/files").json()["files"]
        if f.get("ignored")
    }
    assert ignored == {"a.env", "logs/app.log", "logs/old/1.log", ".claude/settings.local.json"}


def test_git_제외_폴더가_안쪽_독립_저장소면_그_파일은_보이고_git_폴더는_빠진다(client, env):
    """공개 저장소 안에 따로 둔 비공개 문서 저장소 — `.git` 이 폴더다.
    작업용 복사본(`.git` 이 파일)은 위 시험처럼 계속 빠진다."""
    repo = env["repo"]
    (repo / ".gitignore").write_text(".private/\n", "utf-8")
    private = repo / ".private"
    (private / "notes").mkdir(parents=True)
    git("init", "-q", cwd=private)
    (private / "가이드.md").write_text("x", encoding="utf-8")
    (private / "notes" / "a.md").write_text("x", encoding="utf-8")
    key = add(client, env)
    ignored = {
        f["path"]
        for f in client.get(f"/api/projects/{key}/files").json()["files"]
        if f.get("ignored")
    }
    assert ignored == {".private/가이드.md", ".private/notes/a.md"}


def test_git_이_아니면_폴더를_훑되_무거운_폴더는_건너뛴다(client, env):
    plain = env["tmp"] / "plain"
    (plain / "node_modules" / "x").mkdir(parents=True)
    (plain / "node_modules" / "x" / "i.js").write_text("", encoding="utf-8")
    (plain / "docs").mkdir()
    (plain / "docs" / "a.md").write_text("", encoding="utf-8")
    key = client.post("/api/projects", json={"root": str(plain)}).json()["key"]
    out = client.get(f"/api/projects/{key}/files").json()
    assert [f["path"] for f in out["files"]] == ["docs/a.md"]


def test_세션은_작업_폴더로_자동_소속이고_기록이_없으면_훅의_시각을_쓴다(client, env):
    key = add(client, env)
    choices = {c["id"]: c for c in client.get(f"/api/projects/{key}/session-choices").json()}
    assert choices["s-1"]["auto"] and choices["s-1"]["member"]
    assert not choices["s-2"]["auto"] and not choices["s-3"]["auto"]  # 다른 폴더 · 없는 폴더
    store.update_session(key, "s-1", name="develope", last_at="2026-11-12T10:00:00")
    (s,) = client.get(f"/api/projects/{key}/sessions").json()
    assert (s["name"], s["group"], s["state"], s["activity"]) == ("develope", "G", "on", "working")
    assert (s["replies"], s["lastAt"]) == ([], "2026-11-12T10:00:00")
    assert registry.load(env["sessions"])["sessions"] == {}  # 자동 소속은 적어 두지 않는다


def test_세션_한_줄은_대화_기록에서_답_시각을_뽑는다(client, env, monkeypatch):
    sid = "11111111-2222-3333-4444-555555555555"
    monkeypatch.setattr(records.chat, "PROJECTS", env["tmp"] / "claude-projects")
    log = env["tmp"] / "claude-projects" / "C--repo" / f"{sid}.jsonl"
    log.parent.mkdir(parents=True)

    def say(role, content, at):
        return {"type": role, "timestamp": at, "message": {"content": content}}

    lines = [
        say("user", "첫 질문", "2026-11-13T01:00:00Z"),
        say("assistant", [{"type": "text", "text": "첫 답"}], "2026-11-13T01:01:00Z"),
        say("user", "\n  둘째 질문 첫 줄\n둘째 줄", "2026-11-13T02:00:00Z"),
        say("assistant", [{"type": "text", "text": "둘째 답"}], "2026-11-13T02:05:00Z"),
    ]
    log.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), "utf-8")
    env["base"][:] = [row("n-1", "새 세션", env["repo"], conversation=sid)]
    key = add(client, env)
    (s,) = client.get(f"/api/projects/{key}/sessions").json()
    assert s["state"] == "off" and s["activity"] is None
    assert s["replies"] == ["2026-11-13T01:01:00Z", "2026-11-13T02:05:00Z"]
    assert s["lastAt"] == "2026-11-13T02:05:00Z"


def test_세션_고르기는_바꾼_것만_적고_자동과_같으면_지운다(client, env):
    key = add(client, env)
    # 자동 소속 s-1 을 빼고, 다른 폴더의 s-2 를 넣는다
    client.put(f"/api/projects/{key}/sessions", json={"ids": ["s-2"]})
    saved = registry.load(env["sessions"])["sessions"]
    assert saved["s-1"]["project"] == "" and saved["s-2"]["project"] == key
    assert [s["id"] for s in client.get(f"/api/projects/{key}/sessions").json()] == ["s-2"]
    # 되돌리면 적어 둔 것이 사라지고 다시 자동이 된다
    client.put(f"/api/projects/{key}/sessions", json={"ids": ["s-1"]})
    assert all("project" not in e for e in registry.load(env["sessions"])["sessions"].values())


def test_다른_프로젝트에_속한_세션은_표시하고_옮기면_한_곳에만_속한다(client, env):
    key = add(client, env)
    other = client.post("/api/projects", json={"root": str(env["other"])}).json()["key"]
    # s-2 는 작업 폴더로 other 소속이다
    choices = {c["id"]: c for c in client.get(f"/api/projects/{key}/session-choices").json()}
    assert choices["s-2"]["elsewhere"] and not choices["s-2"]["member"]
    client.put(f"/api/projects/{key}/sessions", json={"ids": ["s-1", "s-2"]})
    assert client.get(f"/api/projects/{other}/sessions").json() == []


def test_훅의_시각은_다른_프로젝트에_남은_기록에서도_찾는다(client, env):
    key = add(client, env)
    other = client.post("/api/projects", json={"root": str(env["other"])}).json()["key"]
    client.put(f"/api/projects/{key}/sessions", json={"ids": ["s-1", "s-2"]})
    store.update_session(other, "s-2", last_at="2026-11-12T09:00:00")
    s = {s["id"]: s for s in client.get(f"/api/projects/{key}/sessions").json()}["s-2"]
    assert s["lastAt"] == "2026-11-12T09:00:00" and s["state"] == "off"


# --- 오른쪽: git ---


def test_git_이력은_제목의_할_일_번호와_커밋_안_된_변경을_준다(client, env):
    key = add(client, env)
    (env["repo"] / "src" / "a.py").write_text("changed", encoding="utf-8")
    git("commit", "-qam", "확인 창 [T-12] [T-13]\n\n본문의 [T-99] 는 잇지 않는다", cwd=env["repo"])
    git("mv", "src/a.py", "src/renamed.py", cwd=env["repo"])
    (env["repo"] / "새 파일.txt").write_text("n", encoding="utf-8")
    out = client.get(f"/api/projects/{key}/git").json()
    assert out["git"] is True
    assert [c["tasks"] for c in out["commits"]] == [[12, 13], [1]]
    assert out["commits"][0]["subject"] == "확인 창 [T-12] [T-13]"
    assert {(c["status"], c["path"]) for c in out["changes"]} == {
        ("R", "src/renamed.py"),
        ("??", "새 파일.txt"),
    }


def test_git_이_아니면_비우고_그렇다고_알린다(client, env):
    key = client.post("/api/projects", json={"root": str(env["other"])}).json()["key"]
    assert client.get(f"/api/projects/{key}/git").json() == {
        "git": False,
        "commits": [],
        "changes": [],
    }


def test_커밋을_누르면_바뀐_파일과_줄_수와_변경_덩어리를_준다(client, env):
    key = add(client, env)
    repo = env["repo"]
    (repo / "src" / "a.py").write_text("1\n2\n3\n", encoding="utf-8")
    git("commit", "-qam", "셋째 줄까지", cwd=repo)
    (repo / "src" / "a.py").write_text("1\n둘\n3\n4\n", encoding="utf-8")
    git("mv", ".gitignore", "무시.txt", cwd=repo)
    (repo / "새 파일.md").write_text("n\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "고치고 옮기고 더함 [T-3]\n\n본문 설명", cwd=repo)
    head = client.get(f"/api/projects/{key}/git").json()["commits"][0]
    assert head["author"] == "t" and head["incoming"] is False and len(head["hash"]) == 40

    d = client.get(f"/api/projects/{key}/commits/{head['hash'][:7]}").json()
    assert (d["subject"], d["body"], d["tasks"]) == ("고치고 옮기고 더함 [T-3]", "본문 설명", [3])
    files = {f["path"]: f for f in d["files"]}
    assert (
        files["src/a.py"]["status"],
        files["src/a.py"]["added"],
        files["src/a.py"]["deleted"],
    ) == ("M", 2, 1)
    assert files["무시.txt"]["status"] == "R" and files["무시.txt"]["old"] == ".gitignore"
    assert files["새 파일.md"]["status"] == "A"

    url = f"/api/projects/{key}/commits/{d['hash']}/diff"
    (hunk,) = client.get(url, params={"path": "src/a.py"}).json()["hunks"]
    assert hunk["lines"] == [[" ", "1"], ["-", "2"], ["+", "둘"], [" ", "3"], ["+", "4"]]
    moved = client.get(url, params={"path": "무시.txt", "old": ".gitignore"}).json()
    assert moved["hunks"] == [] and moved["binary"] is False  # 내용이 같은 이름 바꾸기


def test_첫_커밋은_빈_폴더와_비교하고_전체_보기는_파일을_다_준다(client, env):
    key = add(client, env)
    (first,) = client.get(f"/api/projects/{key}/git").json()["commits"]
    d = client.get(f"/api/projects/{key}/commits/{first['hash']}").json()
    assert {f["path"] for f in d["files"]} == {".gitignore", "src/a.py"}
    assert all(f["status"] == "A" for f in d["files"])
    repo = env["repo"]
    (repo / "src" / "a.py").write_text("".join(f"{i}\n" for i in range(1, 21)), encoding="utf-8")
    git("commit", "-qam", "스무 줄", cwd=repo)
    (repo / "src" / "a.py").write_text(
        "".join(f"{i}\n" for i in range(1, 20)) + "끝\n", encoding="utf-8"
    )
    git("commit", "-qam", "끝 줄만", cwd=repo)
    head = client.get(f"/api/projects/{key}/git").json()["commits"][0]["hash"]
    url = f"/api/projects/{key}/commits/{head}/diff"
    short = client.get(url, params={"path": "src/a.py"}).json()["hunks"][0]
    full = client.get(url, params={"path": "src/a.py", "full": True}).json()["hunks"][0]
    assert (short["oldStart"], len(short["lines"])) == (17, 5)  # 바뀐 줄 앞 3줄만
    assert (full["oldStart"], len(full["lines"])) == (1, 21)


def test_AI_요약은_처음_한_번만_만들고_저장해_다시_쓴다(client, env, monkeypatch):
    calls = []

    def fake(detail, patch):
        calls.append((detail["subject"], patch))
        return "- 처음 파일을 넣었다"

    monkeypatch.setattr(records.commit_summary_ai, "summarize", fake)
    # 시험 폴더 경로가 프로젝트 키에 다시 들어가 윈도 경로 길이(260자)를 넘는다. 저장 위치만 짧게
    short = Path(tempfile.mkdtemp())
    monkeypatch.setattr(records, "_summary_path", lambda key, commit: short / f"{commit}.json")
    key = add(client, env)
    sha = client.get(f"/api/projects/{key}/git").json()["commits"][0]["hash"]
    url = f"/api/projects/{key}/commits/{sha[:7]}/summary"
    assert client.get(url).json()["text"] is None  # 보기만 해서는 만들지 않는다
    made = client.post(url).json()
    assert made["text"] == "- 처음 파일을 넣었다" and made["model"] == "sonnet"
    assert client.post(url).json() == made and client.get(url).json() == made
    (subject, patch) = calls[0]
    assert len(calls) == 1 and subject == "처음 [T-1]" and "+a" in patch  # 첫 커밋의 diff
    assert (short / f"{sha}.json").exists()  # 짧은 번호로 물어도 전체 번호로 저장
    shutil.rmtree(short)


def test_AI_요약을_만들지_못하면_409_에_이유(client, env, monkeypatch):
    def fail(detail, patch):
        raise records.commit_summary_ai.SummaryError("요약을 만들지 못했습니다: 로그인 필요")

    monkeypatch.setattr(records.commit_summary_ai, "summarize", fail)
    key = add(client, env)
    sha = client.get(f"/api/projects/{key}/git").json()["commits"][0]["hash"]
    r = client.post(f"/api/projects/{key}/commits/{sha}/summary")
    assert r.status_code == 409 and "로그인 필요" in r.json()["error"]
    assert client.get(f"/api/projects/{key}/commits/{sha}/summary").json()["text"] is None


def test_커밋_내용을_못_읽으면_요약을_만들지_않고_깨진_요약은_없는_것으로_본다(
    client, env, monkeypatch
):
    """빈 diff 로 만든 요약이 저장되면 다시 만들 길이 없다."""
    called = []
    monkeypatch.setattr(records.commit_summary_ai, "summarize", lambda d, p: called.append(1))
    monkeypatch.setattr(records, "commit_detail", lambda key, sha: None)
    key = add(client, env)
    sha = client.get(f"/api/projects/{key}/git").json()["commits"][0]["hash"]
    r = client.post(f"/api/projects/{key}/commits/{sha}/summary")
    assert r.status_code == 409 and "읽지 못했습니다" in r.json()["error"] and not called
    broken = env["tmp"] / "broken.json"
    broken.write_text("{반쯤", encoding="utf-8")
    assert (
        records._read_summary(broken) is None and records._read_summary(env["tmp"] / "없음") is None
    )


def test_작업의_바뀐_파일은_그_작업_커밋_전후로_비교한다(client, env):
    """T-19: 이전은 [T-n] 첫 커밋 직전, 이후는 마지막 [T-n] 커밋."""
    key = add(client, env)
    repo = env["repo"]
    write_task(key, 3, "done", "s")
    (repo / "src" / "a.py").write_text("a\nb\n", encoding="utf-8")
    git("commit", "-qam", "하나 [T-3]", cwd=repo)
    (repo / "src" / "a.py").write_text("a\nb\nc\n", encoding="utf-8")
    git("commit", "-qam", "둘 [T-3]", cwd=repo)
    (repo / "src" / "a.py").write_text("a\nb\nc\n나중\n", encoding="utf-8")
    git("commit", "-qam", "다른 작업 [T-9]", cwd=repo)
    d = client.get(f"/api/projects/{key}/tasks/3/diff", params={"path": "src/a.py"}).json()
    assert len(d["commits"]) == 2 and d["uncommitted"] is False
    (hunk,) = d["hunks"]
    # 처음 커밋의 내용('a') → T-3 마지막 커밋('a b c'). 뒤의 T-9 변경('나중')은 들어가지 않는다
    assert hunk["lines"] == [["-", "a"], ["+", "a"], ["+", "b"], ["+", "c"]]


snap = records.snap


def snapshots(
    key: str, path: str, task: str, *steps: tuple[str, str | None], prompt: str = "p"
) -> None:
    """훅이 남겼을 스냅샷을 차례로 적는다. steps: ("before" | "after", 내용 또는 None(없던 파일))."""
    project = store.project_dir(key)
    log = snap.log_path(project)
    with open(log, "a", encoding="utf-8") as f:
        for when, text in steps:
            blob = (
                text
                if text == "unknown"
                else (snap.put(project, text.encode()) if text is not None else None)
            )
            entry = {"task": task, "path": path, "when": when, "blob": blob, "prompt": prompt}
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def test_스냅샷이_있으면_커밋과_상관없이_고치기_직전에서_마지막_직후로(client, env):
    """팀 저장소처럼 커밋 메시지에 [T-n] 이 없어도 된다. 작업 전에 커밋 안 된 변경이 있던 파일도
    '이전' 은 그 작업이 고치기 직전 내용이다."""
    key = add(client, env)
    write_task(key, 4, "done", "s")
    (env["repo"] / "src" / "a.py").write_text("a\nb\n", encoding="utf-8")
    git("commit", "-qam", "feat: 무엇을 왜 (번호 없음)", cwd=env["repo"])
    snapshots(
        key,
        "src/a.py",
        "T-4",
        ("before", "커밋 안 된 변경\n"),
        ("after", "첫 수정\n"),
        ("before", "첫 수정\n"),
        ("after", "마지막\n"),
    )
    d = client.get(f"/api/projects/{key}/tasks/4/diff", params={"path": "src/a.py"}).json()
    assert d["snapshot"] and not d["beforeUnknown"] and d["commits"] == []
    (hunk,) = d["hunks"]
    assert hunk["lines"] == [["-", "커밋 안 된 변경"], ["+", "마지막"]]


def test_스냅샷으로_새로_만든_파일은_전부_더한_줄이다(client, env):
    key = add(client, env)
    write_task(key, 4, "doing", "s")
    snapshots(key, "src/new.py", "T-4", ("before", None), ("after", "새\n파일\n"))
    d = client.get(f"/api/projects/{key}/tasks/4/diff", params={"path": "src/new.py"}).json()
    assert d["snapshot"] and d["hunks"][0]["lines"] == [["+", "새"], ["+", "파일"]]


def test_직전_내용을_모르면_지금_내용만_보이고_알린다(client, env):
    key = add(client, env)
    write_task(key, 4, "doing", "s")
    snapshots(key, "src/a.py", "T-4", ("before", "unknown"), ("after", "지금\n"))
    d = client.get(f"/api/projects/{key}/tasks/4/diff", params={"path": "src/a.py"}).json()
    assert d["snapshot"] and d["beforeUnknown"] and d["hunks"][0]["lines"] == [["+", "지금"]]


def test_git_이_아닌_폴더도_스냅샷으로_비교한다(client, env):
    plain = env["tmp"] / "plain"
    plain.mkdir()
    (plain / "memo.md").write_text("x", encoding="utf-8")
    key = client.post("/api/projects", json={"root": str(plain)}).json()["key"]
    write_task(key, 1, "done", "s")
    snapshots(key, "memo.md", "T-1", ("before", "처음\n"), ("after", "고침\n"))
    d = client.get(f"/api/projects/{key}/tasks/1/diff", params={"path": "memo.md"}).json()
    assert d["snapshot"] and d["hunks"][0]["lines"] == [["-", "처음"], ["+", "고침"]]


def test_다른_작업의_스냅샷은_섞이지_않는다(client, env):
    key = add(client, env)
    write_task(key, 4, "done", "s")
    snapshots(key, "src/a.py", "T-4", ("before", "1\n"), ("after", "2\n"))
    snapshots(key, "src/a.py", "T-5", ("before", "2\n"), ("after", "3\n"))
    d = client.get(f"/api/projects/{key}/tasks/4/diff", params={"path": "src/a.py"}).json()
    assert d["hunks"][0]["lines"] == [["-", "1"], ["+", "2"]]


def test_진행_중_작업은_커밋_안_된_변경과_새_파일까지_보인다(client, env):
    key = add(client, env)
    repo = env["repo"]
    write_task(key, 5, "doing", "s")
    (repo / "src" / "a.py").write_text("a\n고침\n", encoding="utf-8")
    d = client.get(f"/api/projects/{key}/tasks/5/diff", params={"path": "src/a.py"}).json()
    assert d["commits"] == [] and d["uncommitted"] is True
    assert ["+", "고침"] in d["hunks"][0]["lines"]
    (repo / "src" / "new.py").write_text("x = 1\ny = 2\n", encoding="utf-8")  # git 이 아직 모른다
    d = client.get(f"/api/projects/{key}/tasks/5/diff", params={"path": "src/new.py"}).json()
    assert d["hunks"][0]["lines"] == [["+", "x = 1"], ["+", "y = 2"]]
    # 끝난 작업이면 커밋 안 된 변경은 그 작업 것이 아니다 — 비교할 것이 없어 화면이 [파일] 보기로 연다
    write_task(key, 6, "done", "s")
    d = client.get(f"/api/projects/{key}/tasks/6/diff", params={"path": "src/a.py"}).json()
    assert (d["commits"], d["uncommitted"], d["hunks"]) == ([], False, [])


def test_없는_커밋이나_번호_모양이_아니면_404(client, env):
    key = add(client, env)
    for sha in ("deadbeef00", "--help", "HEAD"):
        assert client.get(f"/api/projects/{key}/commits/{sha}").status_code == 404
        assert client.post(f"/api/projects/{key}/commits/{sha}/summary").status_code == 404
        assert (
            client.get(f"/api/projects/{key}/commits/{sha}/diff", params={"path": "x"}).status_code
            == 404
        )


def test_동료가_올린_커밋은_갱신으로_받아_아직_안_받음으로_보인다(client, env):
    repo = env["repo"]
    origin = env["tmp"] / "origin.git"
    git("init", "-q", "--bare", str(origin), cwd=env["tmp"])
    git("remote", "add", "origin", str(origin), cwd=repo)
    git("push", "-q", "-u", "origin", "HEAD", cwd=repo)
    mate = env["tmp"] / "mate"
    git("clone", "-q", str(origin), str(mate), cwd=env["tmp"])
    (mate / "동료.txt").write_text("안녕\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=mate, check=True)
    subprocess.run(
        ["git", "-c", "user.name=동료", "-c", "user.email=m@m", "commit", "-qm", "동료 작업"],
        cwd=mate,
        check=True,
    )
    subprocess.run(["git", "push", "-q"], cwd=mate, check=True, capture_output=True)

    key = add(client, env)
    fetched = client.post(f"/api/projects/{key}/git/fetch").json()
    assert fetched["error"] is None and fetched["at"]
    out = client.get(f"/api/projects/{key}/git").json()
    assert out["upstream"].startswith("origin/") and out["fetch"]["error"] is None
    # 같은 초에 만든 커밋 둘은 시각 순서가 정해지지 않는다 — 자리 대신 제목으로 고른다
    # (윈도우에서 가끔, 리눅스에서는 늘 실패하던 원인)
    by_subject = {c["subject"]: c for c in out["commits"]}
    mine, theirs = by_subject["처음 [T-1]"], by_subject["동료 작업"]
    assert (theirs["author"], theirs["subject"], theirs["incoming"]) == ("동료", "동료 작업", True)
    assert mine["incoming"] is False
    # 받기 전에도 커밋 내용을 볼 수 있다 (fetch 가 커밋을 이미 가져왔다)
    d = client.get(f"/api/projects/{key}/commits/{theirs['hash']}").json()
    assert [f["path"] for f in d["files"]] == ["동료.txt"]


def test_원격이_없으면_fetch_정보도_없다(client, env):
    key = add(client, env)
    out = client.get(f"/api/projects/{key}/git").json()
    assert out["fetch"] is None and out["upstream"] is None
    assert client.post(f"/api/projects/{key}/git/fetch").json()["error"] == "원격 저장소가 없습니다"


# --- 경계 사례 ---


def test_작업용_복사본에서_일하는_세션도_원래_저장소로_모은다(client, env):
    """후보 · 추가 · 미리 체크가 모두 원래 저장소 기준이어야 한다."""
    wt = env["repo"] / ".claude" / "worktrees" / "bg"
    git("worktree", "add", "-q", str(wt), "-b", "bg", cwd=env["repo"])
    env["base"].append(row("s-4", "bg-head", wt / "src"))
    (cand,) = [c for c in client.get("/api/projects/candidates").json() if c["name"] == "repo"]
    assert sorted(cand["sessions"]) == ["bg-head", "develope"]
    key = client.post("/api/projects", json={"root": str(wt)}).json()["key"]
    assert key == store.project_key(store.main_path(str(env["repo"])))
    choices = {c["id"]: c for c in client.get(f"/api/projects/{key}/session-choices").json()}
    assert choices["s-4"]["auto"] and choices["s-1"]["auto"]


@pytest.mark.parametrize(
    ("given", "error"),
    [
        ("relative/path", "전체 경로"),
        ("", "전체 경로"),
        ("C:\\없는\\폴더" if os.name == "nt" else "/없는/폴더", "폴더가 없습니다"),
    ],
)
def test_추가할_경로는_실제로_있는_전체_경로여야_한다(client, given, error):
    r = client.post("/api/projects", json={"root": given})
    assert r.status_code == 409 and error in r.json()["error"]


def test_경로의_따옴표와_공백은_떼고_같은_폴더는_두_번_등록하지_않는다(client, env):
    quoted = '  "' + str(env["repo"]) + '"  '
    r = client.post("/api/projects", json={"root": quoted, "name": "  처음  "})
    assert r.status_code == 200 and r.json()["name"] == "처음"
    again = client.post("/api/projects", json={"root": str(env["repo"]), "name": "다른 이름"})
    assert again.status_code == 409 and "이미 등록된" in again.json()["error"]


def test_폴더가_사라지면_git_아님이_아니라_폴더_없음으로_알린다(client, env):
    key = client.post("/api/projects", json={"root": str(env["other"])}).json()["key"]
    env["other"].rmdir()
    for url in ("files", "git"):
        r = client.get(f"/api/projects/{key}/{url}")
        assert r.status_code == 409 and "폴더가 없습니다" in r.json()["error"]
    assert client.get(f"/api/projects/{key}/tasks").status_code == 200  # 기록은 그대로 보인다


def test_커밋이_없는_저장소도_git_이고_새_파일을_보인다(client, env):
    empty = env["tmp"] / "empty"
    empty.mkdir()
    git("init", "-q", cwd=empty)
    (empty / "a.txt").write_text("a", encoding="utf-8")
    key = client.post("/api/projects", json={"root": str(empty)}).json()["key"]
    out = client.get(f"/api/projects/{key}/git").json()
    assert out["git"] is True and out["commits"] == []
    assert out["changes"] == [{"status": "??", "path": "a.txt"}]


def test_작업_트리의_이름_바꾸기도_옛_이름을_건너뛴다(client, env):
    key = add(client, env)
    (env["repo"] / "src" / "a.py").rename(env["repo"] / "src" / "b.py")
    git("add", "-N", "src/b.py", cwd=env["repo"])  # ' R' 처럼 둘째 자리에 R 이 온다
    changes = client.get(f"/api/projects/{key}/git").json()["changes"]
    assert [c["path"] for c in changes] == ["src/b.py"]  # 옛 이름 조각이 상태 줄로 끼지 않는다


def test_안쪽의_다른_저장소는_파일_목록에_끼지_않는다(client, env):
    key = add(client, env)
    nested = env["repo"] / "nested"
    nested.mkdir()
    git("init", "-q", cwd=nested)
    (nested / "x.txt").write_text("x", encoding="utf-8")
    paths = [f["path"] for f in client.get(f"/api/projects/{key}/files").json()["files"]]
    assert not any(p.endswith("/") for p in paths)


def test_생긴_작업은_첫_만들기_기록이다(client, env):
    key = add(client, env)
    store.append_activity(key, {"op": "create", "path": "src/a.py", "task": "T-1"})
    store.append_activity(key, {"op": "create", "path": "src/a.py", "task": "T-2"})
    rows = client.get(f"/api/projects/{key}/files").json()["files"]
    a = next(f for f in rows if f["path"] == "src/a.py")
    assert (a["createdTask"], a["lastTask"]) == ("T-1", "T-2")


def test_git_이_아닌_큰_폴더는_잘렸다고_알린다(client, env, monkeypatch):
    for i in range(3):
        (env["other"] / f"{i}.txt").write_text("", encoding="utf-8")
    key = client.post("/api/projects", json={"root": str(env["other"])}).json()["key"]
    monkeypatch.setattr(records, "WALK_LIMIT", 2)
    out = client.get(f"/api/projects/{key}/files").json()
    assert out["truncated"] is True and len(out["files"]) == 2


def test_변경_기록은_파일이_바뀔_때만_다시_읽는다(client, env, monkeypatch):
    key = add(client, env)
    store.append_activity(key, {"op": "create", "path": "src/a.py", "task": "T-1"})
    calls = []
    real = store.read_activity
    monkeypatch.setattr(store, "read_activity", lambda k: calls.append(k) or real(k))
    client.get(f"/api/projects/{key}/files")
    client.get(f"/api/projects/{key}/files")
    assert len(calls) == 1
    store.append_activity(key, {"op": "modify", "path": "src/a.py", "task": "T-1"})
    rows = client.get(f"/api/projects/{key}/files").json()["files"]
    assert len(calls) == 2
    assert next(x for x in rows if x["path"] == "src/a.py")["changes"] == 2


def test_깨진_할_일_파일도_목록이_죽지_않고_문제로_보인다(client, env):
    key = add(client, env)
    folder = store.project_dir(key) / "tasks"
    (folder / "T-5-cp949.md").write_bytes("---\nstatus: doing\n---\n# T-5 한글\n".encode("cp949"))
    six = write_task(key, 6, "todo", "")
    (folder / "T-6-dup.md").write_text(six.read_text("utf-8"), encoding="utf-8")
    rows = client.get(f"/api/projects/{key}/tasks").json()
    five = next(r for r in rows if r["number"] == 5)
    assert five["status"] == "" and five["problems"]
    sixes = [r for r in rows if r["number"] == 6]
    assert len({r["file"] for r in sixes}) == 2
    assert all(any("여럿" in p for p in r["problems"]) for r in sixes)
    assert client.get(f"/api/projects/{key}/tasks/5").status_code == 404


def test_창을_연_사이_삭제된_세션이_있으면_저장하지_않고_다시_열게_한다(client, env):
    """그대로 저장하면 그 사이 생긴 변화가 조용히 섞인다."""
    key = add(client, env)
    r = client.put(f"/api/projects/{key}/sessions", json={"ids": ["s-지워짐", "s-2"]})
    assert r.status_code == 409 and "다시 열어" in r.json()["error"]
    assert registry.load(env["sessions"])["sessions"] == {}


def test_모르는_프로젝트로는_세션을_넣지_않는다(client, env):
    assert client.put("/api/projects/없음/sessions", json={"ids": ["s-1"]}).status_code == 404
    assert registry.load(env["sessions"])["sessions"] == {}


def test_폴더_선택_창이_열려_있으면_또_띄우지_않는다(client, monkeypatch):
    from devdash.api import project_routes

    assert project_routes._picking.acquire(blocking=False)
    try:
        r = client.post("/api/projects/pick-folder")
        assert r.status_code == 409 and "이미 열려" in r.json()["error"]
    finally:
        project_routes._picking.release()

    def broken():
        raise OSError("powershell.exe 없음")

    monkeypatch.setattr(local, "pick_folder", broken)
    assert client.post("/api/projects/pick-folder").status_code == 409


def test_같은_번호가_둘이면_화면이_고른_파일의_정리_문서를_준다(client, env):
    key = add(client, env)
    write_task(key, 1, "doing", "develope")
    other = store.project_dir(key) / "tasks" / "T-1-y.md"
    other.write_text(
        (store.project_dir(key) / "tasks" / "T-1-x.md")
        .read_text("utf-8")
        .replace("할 일 1", "둘째"),
        encoding="utf-8",
    )
    assert client.get(f"/api/projects/{key}/tasks/1").json()["file"] == "T-1-x.md"
    d = client.get(f"/api/projects/{key}/tasks/1", params={"file": "T-1-y.md"}).json()
    assert (d["file"], d["title"]) == ("T-1-y.md", "둘째")
    assert client.get(f"/api/projects/{key}/tasks/1", params={"file": "없음.md"}).status_code == 404


# --- 순서 바꾸기 · 이름 바꾸기 · 등록 해제 ---


def test_끌어서_정한_순서대로_목록이_나오고_새_프로젝트는_뒤에_붙는다(client, env):
    a = add(client, env, "가")
    b = client.post("/api/projects", json={"root": str(env["other"]), "name": "나"}).json()["key"]
    assert [p["key"] for p in client.get("/api/projects").json()] == [a, b]  # 처음엔 이름순
    assert client.put("/api/projects/order", json={"keys": [b, a, "없는키"]}).json() == {"ok": True}
    assert [p["key"] for p in client.get("/api/projects").json()] == [b, a]
    plain = env["tmp"] / "plain"
    plain.mkdir()
    c = client.post("/api/projects", json={"root": str(plain), "name": "가가"}).json()["key"]
    assert [p["key"] for p in client.get("/api/projects").json()] == [b, a, c]


def test_이름을_바꾸면_목록에_보이고_빈_이름은_받지_않는다(client, env):
    key = add(client, env)
    r = client.patch(f"/api/projects/{key}", json={"name": "  새 이름  "})
    assert r.status_code == 200 and r.json()["name"] == "새 이름"
    assert client.get("/api/projects").json()[0]["name"] == "새 이름"
    assert client.patch(f"/api/projects/{key}", json={"name": " "}).status_code == 409
    assert client.patch("/api/projects/없음", json={"name": "x"}).status_code == 404


def test_등록을_풀면_목록과_세션_소속에서_빠지고_다시_추가하면_기록이_이어진다(client, env):
    key = add(client, env)
    write_task(key, 1, "doing", "develope")
    client.put(f"/api/projects/{key}/sessions", json={"ids": ["s-1", "s-2"]})
    client.put("/api/projects/order", json={"keys": [key]})
    assert client.delete(f"/api/projects/{key}").json() == {"ok": True}
    assert client.get("/api/projects").json() == []
    assert store.find_project(str(env["repo"])) is None  # 훅도 더는 기록하지 않는다
    # 보이지 않는 프로젝트로 정해 둔 세션은 자동 소속으로 돌아간다
    assert all("project" not in e for e in registry.load(env["sessions"])["sessions"].values())
    assert json.loads((store.home() / "project-order.json").read_text("utf-8")) == []
    again = add(client, env)
    assert again == key and [
        t["number"] for t in client.get(f"/api/projects/{key}/tasks").json()
    ] == [1]
    assert client.delete("/api/projects/없음").status_code == 404


def test_파일을_바꾼_할_일들을_최근_순으로_준다(client, env):
    key = add(client, env)
    for at, op, path, task in [
        ("2026-11-13T09:00:00", "create", "src/a.py", "T-1"),
        ("2026-11-13T10:00:00", "modify", "src/a.py", "T-2"),
        ("2026-11-13T11:00:00", "modify", "src/a.py", None),
        ("2026-11-13T12:00:00", "modify", "src/a.py", "T-2"),
        ("2026-11-13T12:30:00", "modify", "src/b.py", "T-3"),
    ]:
        store.append_activity(key, {"at": at, "op": op, "path": path, "task": task})
    rows = client.get(f"/api/projects/{key}/file-history", params={"path": "src/a.py"}).json()
    assert [(r["task"], r["changes"], r["created"], r["last"][11:16]) for r in rows] == [
        ("T-2", 2, False, "12:00"),
        (None, 1, False, "11:00"),
        ("T-1", 1, True, "09:00"),
    ]
    assert client.get(f"/api/projects/{key}/file-history", params={"path": "없음"}).json() == []


# --- [파일] 보기의 문서 읽기 ---


def test_프로젝트_안의_글_파일은_내용을_준다(client, env):
    key = add(client, env)
    (env["repo"] / "docs").mkdir()
    (env["repo"] / "docs" / "한글 문서.md").write_bytes("\ufeff# 제목\n\n본문".encode("utf-8"))
    r = client.get(f"/api/projects/{key}/file", params={"path": "docs/한글 문서.md"})
    assert r.status_code == 200 and r.json() == {
        "path": "docs/한글 문서.md",
        "text": "# 제목\n\n본문",
    }


WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="윈도우만 \\ 가 경로 구분 기호다")
OUTSIDE_ABS = r"C:\Windows\win.ini" if os.name == "nt" else "/etc/hostname"


@pytest.mark.parametrize(
    "path",
    ["../other/x.md", pytest.param(r"..\other\x.md", marks=WINDOWS_ONLY), OUTSIDE_ABS, "", "."],
)
def test_프로젝트_폴더_밖은_열지_않는다(client, env, path):
    key = add(client, env)
    (env["other"] / "x.md").write_text("비밀", encoding="utf-8")
    r = client.get(f"/api/projects/{key}/file", params={"path": path})
    assert r.status_code == 409 and "밖의 파일" in r.json()["error"]


def test_정션으로_밖을_가리켜도_열지_않는다(client, env):
    key = add(client, env)
    (env["other"] / "x.md").write_text("비밀", encoding="utf-8")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(env["other"]), str(env["repo"] / "j"))
    else:
        os.symlink(env["other"], env["repo"] / "j", target_is_directory=True)
    r = client.get(f"/api/projects/{key}/file", params={"path": "j/x.md"})
    assert r.status_code == 409


def test_없거나_크거나_글이_아닌_파일은_이유를_알린다(client, env, monkeypatch):
    key = add(client, env)
    assert client.get(f"/api/projects/{key}/file", params={"path": "없음.md"}).status_code == 404
    (env["repo"] / "bin.dat").write_bytes(b"\xff\xfe\x00\x81")
    r = client.get(f"/api/projects/{key}/file", params={"path": "bin.dat"})
    assert r.status_code == 409 and "UTF-8" in r.json()["error"]
    monkeypatch.setattr(records, "READ_LIMIT", 3)
    r = client.get(f"/api/projects/{key}/file", params={"path": "src/a.py"})
    assert r.status_code == 200  # 1바이트
    (env["repo"] / "big.md").write_text("12345", encoding="utf-8")
    r = client.get(f"/api/projects/{key}/file", params={"path": "big.md"})
    assert r.status_code == 409 and "커서" in r.json()["error"]


# --- 그림 파일 ---

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.png", "image/png"),
        ("사진.JPG", "image/jpeg"),
        ("b.svg", "image/svg+xml"),
        ("c.ico", "image/x-icon"),
    ],
)
def test_그림은_형식에_맞는_Content_Type_과_보안_머리말로_준다(client, env, name, kind):
    key = add(client, env)
    (env["repo"] / "images").mkdir()
    (env["repo"] / "images" / name).write_bytes(PNG)
    r = client.get(f"/api/projects/{key}/image", params={"path": f"images/{name}"})
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"].startswith(kind)
    assert r.headers["x-content-type-options"] == "nosniff"
    # svg 를 주소창으로 직접 열어도 스크립트가 돌지 않게
    assert "sandbox" in r.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["../other/x.png", OUTSIDE_ABS, ""])
def test_그림도_프로젝트_폴더_밖은_열지_않는다(client, env, path):
    key = add(client, env)
    (env["other"] / "x.png").write_bytes(PNG)
    r = client.get(f"/api/projects/{key}/image", params={"path": path})
    assert r.status_code == 409 and "밖의 파일" in r.json()["error"]


def test_그림이_아니거나_크거나_없으면_이유를_알린다(client, env, monkeypatch):
    key = add(client, env)
    r = client.get(f"/api/projects/{key}/image", params={"path": "src/a.py"})
    assert r.status_code == 409 and "그림 파일" in r.json()["error"]
    assert client.get(f"/api/projects/{key}/image", params={"path": "없음.png"}).status_code == 404
    (env["repo"] / "big.png").write_bytes(PNG)
    monkeypatch.setattr(records, "IMAGE_LIMIT", 4)
    r = client.get(f"/api/projects/{key}/image", params={"path": "big.png"})
    assert r.status_code == 409 and "커서" in r.json()["error"]


def chat_messages(*prompts: str) -> list[dict]:
    out = []
    for p in prompts:
        out += [
            {"role": "user", "text": "q", "at": None, "prompt": p},
            {"role": "assistant", "text": "a", "at": None, "prompt": p},
        ]
    return out


def test_대화의_답마다_그_차례에_고친_파일을_붙인다(client, env):
    """원본은 훅의 변경 기록과 스냅샷. 할 일 · 줄 수 · 셸 · 하위 작업 표시, 그 차례의 비교."""
    key = add(client, env)
    write_task(key, 4, "doing", "s")
    for row in [
        {"op": "modify", "path": "src/a.py", "task": "T-4", "prompt": "p1", "tool": "Edit"},
        {"op": "create", "path": "src/new.py", "task": "T-4", "prompt": "p1", "agent": "x"},
        {"op": "modify", "path": "src/a.py", "task": "T-4", "prompt": "p1", "tool": "Bash"},
        {"op": "modify", "path": "src/old.py", "task": None, "prompt": "p2", "tool": "Edit"},
        {"op": "modify", "path": "src/a.py", "task": "T-4", "prompt": "다른 대화", "tool": "Edit"},
    ]:
        store.append_activity(key, row)
    snapshots(key, "src/a.py", "T-4", ("before", "a\nb\n"), ("after", "a\nB\nc\n"), prompt="p1")
    snapshots(key, "src/new.py", "T-4", ("before", None), ("after", "새\n"), prompt="p1")

    messages = chat_messages("p1", "p2", "p3")
    records.chat_work(key, messages)
    w1, w2 = messages[1]["work"], messages[3]["work"]
    assert "work" not in messages[0] and "work" not in messages[5]  # 질문 · 고친 것 없는 차례
    assert w1["task"] == {"number": 4, "title": records.task_list(key)[0]["title"]}
    a, new = w1["files"]
    assert (a["path"], a["op"], a["added"], a["deleted"], a["diff"]) == (
        "src/a.py",
        "edit",
        2,
        1,
        True,
    )
    assert a["shell"] and not a["agent"]
    assert (new["op"], new["added"], new["agent"], new["shell"]) == ("create", 1, True, False)
    # 스냅샷이 없는 옛 차례: 파일 이름만, 비교는 없다
    assert w2["task"] is None
    assert w2["files"] == [
        {
            "path": "src/old.py",
            "op": "edit",
            "added": None,
            "deleted": None,
            "diff": False,
            "shell": False,
            "agent": False,
        }
    ]

    d = client.get(f"/api/projects/{key}/turns/p1/diff", params={"path": "src/a.py"}).json()
    assert d["hunks"][0]["lines"] == [[" ", "a"], ["-", "b"], ["+", "B"], ["+", "c"]]
    r = client.get(f"/api/projects/{key}/turns/p2/diff", params={"path": "src/old.py"})
    assert r.status_code == 404


def test_스냅샷_용량을_보이고_등록_해제_때_고르면_지운다(client, env):
    """자동으로 지우지 않는다. 등록 해제에서 고른 때만 (긴 경로 · 읽기 전용 git 파일도)."""
    key = add(client, env)
    project = store.project_dir(key)
    assert client.get(f"/api/projects/{key}/snapshots").json() == {"bytes": 0}
    snapshots(key, "src/a.py", "T-1", ("before", "a\n" * 100), ("after", "b\n" * 100))
    size = client.get(f"/api/projects/{key}/snapshots").json()["bytes"]
    assert size > snap.log_path(project).stat().st_size  # 기록 줄 + 저장소 안의 내용

    # 고르지 않으면 남는다 (다시 추가하면 비교도 이어진다)
    assert client.delete(f"/api/projects/{key}").json() == {"ok": True}
    assert snap.repo(project).exists() and snap.log_path(project).exists()
    key = add(client, env)
    assert client.get(f"/api/projects/{key}/snapshots").json()["bytes"] == size

    assert client.delete(f"/api/projects/{key}", params={"snapshots": True}).json() == {"ok": True}
    assert not snap.repo(project).exists() and not snap.log_path(project).exists()
    assert (project / "project.removed.json").exists()  # 다른 기록은 그대로
