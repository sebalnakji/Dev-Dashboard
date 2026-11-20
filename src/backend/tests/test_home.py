"""사용자 상태 위치(~/.devdash) 테스트. 어느 폴더에서 불러도 같은 곳을 보는지, 설정이 없어도 뜨는지."""

from __future__ import annotations

import logging

import pytest

from devdash import home as home_mod
from devdash.config import load_config
from devdash.logging_setup import setup_logging


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "devdash-home"
    monkeypatch.setenv("DEVDASH_HOME", str(h))
    elsewhere = tmp_path / "아무-폴더"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # 현재 폴더 기준으로 찾으면 여기서 못 찾는다
    return h


def test_모든_사용자_상태는_DEVDASH_HOME_아래에_있다(home):
    assert home_mod.home() == home
    assert home_mod.servers_path() == home / "servers.yaml"
    assert home_mod.sessions_path() == home / "sessions.json"
    assert home_mod.log_dir() == home / "logs"


def test_플러그인과_같은_위치_계산을_쓴다(home):
    import devdash_store

    assert home_mod.home() == devdash_store.home()


def test_서버_설정이_없으면_서버_0대로_뜬다(home):
    assert load_config().servers == []


@pytest.mark.parametrize("text", ["servers: []\n", "", "# 주석만\n"])
def test_빈_서버_설정도_서버_0대(home, text):
    home.mkdir()
    (home / "servers.yaml").write_text(text, encoding="utf-8")
    assert load_config().servers == []


def test_어느_폴더에서_불러도_같은_서버_설정을_읽는다(home):
    home.mkdir()
    (home / "servers.yaml").write_text(
        "servers:\n  - id: a\n    host: 10.0.0.1\n    user: u\n", encoding="utf-8"
    )
    assert [s.id for s in load_config().servers] == ["a"]


def test_로그는_현재_폴더가_아니라_DEVDASH_HOME_에_쓴다(home, tmp_path):
    try:
        setup_logging()
        logging.getLogger("test").info("기록")
        assert (home / "logs" / "devdash.log").exists()
        assert not (tmp_path / "아무-폴더" / "logs").exists()
    finally:
        # 다음 테스트가 임시 폴더의 로그 파일을 잡고 있지 않게 풀어 둔다
        root = logging.getLogger()
        for handler in list(root.handlers):
            handler.close()
            root.removeHandler(handler)


def test_세션_배치도_DEVDASH_HOME_에_쓴다(home):
    from devdash.claude import registry

    with registry.editing() as data:
        registry.add_group(data, "G")
    assert registry.load()["groups"] == [{"name": "G", "collapsed": False}]
    assert (home / "sessions.json").exists()


def test_예전_설정의_control_항목은_무시하고_읽는다(home):
    """읽는 곳이 없어 지운 설정 — 이미 적어 둔 사람의 설정 파일이 깨지지 않게."""
    home.mkdir()
    (home / "servers.yaml").write_text(
        "servers:\n  - id: a\n    host: 10.0.0.1\n    user: u\n    control: true\n",
        encoding="utf-8",
    )
    (server,) = load_config().servers
    assert server.id == "a" and not hasattr(server, "control")
