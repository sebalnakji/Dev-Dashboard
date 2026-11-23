"""코드 상태 견주기 (codestate) — 임시 git 저장소로. 이 폴더의 실제 상태는 보지 않는다."""

from __future__ import annotations

import subprocess

import pytest

from devdash import codestate


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    front = tmp_path / "src" / "frontend"
    (front / "dist").mkdir(parents=True)
    (front / "dist" / "index.html").write_text("빌드본", encoding="utf-8")
    (front / "a.ts").write_text("1", encoding="utf-8")
    (front / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "server.py").write_text("1", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "처음")
    monkeypatch.setattr(codestate, "REPO", tmp_path)
    monkeypatch.setattr(codestate, "BUILD_STAMP", front / "dist" / ".devdash-build.json")
    return tmp_path


def commit(repo, path, text):
    (repo / path).write_text(text, encoding="utf-8")
    git(repo, "commit", "-qam", f"{path} 고침")


def test_지금_코드는_커밋과_고친_채인지(repo):
    now = codestate.current()
    assert len(now["commit"]) == 40 and now["clean"] is True
    (repo / "server.py").write_text("2", encoding="utf-8")
    assert codestate.current()["clean"] is False
    (repo / "server.py").write_text("1", encoding="utf-8")
    (repo / "새 메모.txt").write_text("x", encoding="utf-8")  # 새로 만든 파일은 세지 않는다
    assert codestate.current()["clean"] is True


def test_git_이_아니면_견줄_수_없어_같다고_본다(tmp_path, monkeypatch):
    monkeypatch.setattr(codestate, "REPO", tmp_path)
    now = codestate.current()
    assert now == {"commit": None, "clean": None} and codestate.same(None, now)


@pytest.mark.parametrize(
    ("running", "expected"),
    [
        ({"commit": "c1", "clean": True}, True),
        ({"commit": "c0", "clean": True}, False),
        ({"commit": "c1", "clean": False}, False),
        (None, False),
    ],
)
def test_떠_있는_서버와_지금_코드_견주기(running, expected):
    assert codestate.same(running, {"commit": "c1", "clean": True}) is expected
    assert (
        codestate.same({"commit": "c1", "clean": True}, {"commit": "c1", "clean": False}) is False
    )


def test_화면은_빌드한_뒤_화면_코드가_바뀌었을_때만_다시_빌드한다(repo):
    assert codestate.frontend_build_needed() == (True, False)  # 빌드한 커밋을 모른다
    codestate.write_build_stamp()
    assert codestate.frontend_build_needed() == (False, False)  # 그대로
    commit(repo, "server.py", "2")
    assert codestate.frontend_build_needed() == (False, False)  # 서버 코드만 바뀜
    commit(repo, "src/frontend/a.ts", "2")
    assert codestate.frontend_build_needed() == (True, False)  # 화면 코드가 바뀜
    codestate.write_build_stamp()
    commit(repo, "src/frontend/package-lock.json", '{"v": 2}')
    assert codestate.frontend_build_needed() == (True, True)  # 패키지부터 다시 받는다
    codestate.write_build_stamp()
    (repo / "src" / "frontend" / "a.ts").write_text("고치는 중", encoding="utf-8")
    assert codestate.frontend_build_needed()[0] is True  # 화면 코드를 고친 채
    (repo / "src" / "frontend" / "dist" / "index.html").unlink()
    assert codestate.frontend_build_needed() == (True, False)  # 빌드본이 없다
