"""모든 테스트가 실제 사용자 상태(~/.devdash)를 읽거나 쓰지 않게 한다.

설정 · 세션 목록 · 로그를 기본 위치에서 찾는 코드가 많아서, 테스트 하나가 깜빡하면 실제 서버 설정을
읽어 SSH 접속을 하거나 실제 세션 목록을 고칠 수 있다. 테스트가 따로 정하면 그 값이 이긴다.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _devdash_home(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path_factory.mktemp("devdash-home")))


@pytest.fixture(autouse=True)
def _no_warm_up(monkeypatch):
    """서버를 띄우는 시험이 실제 claude 목록을 미리 읽지 않게 (devdash.app._warm_up)."""
    monkeypatch.setenv("DEVDASH_NO_WARMUP", "1")
