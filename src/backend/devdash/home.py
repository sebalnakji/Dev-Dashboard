"""사용자 상태가 있는 곳을 정하는 단 한 곳.

설정 · 세션 목록 · 로그 · 실행 정보는 모두 `~/.devdash` 아래에 둔다 (`DEVDASH_HOME` 이 있으면 그곳).
저장소 밖 한곳에 두어야 어느 폴더에서 실행해도 같은 설정을 보고,
플러그인 훅(설치 캐시 사본에서 돈다)과 대시보드가 같은 상태 한 벌을 본다.

위치 계산 자체는 플러그인의 `devdash_store.home()` 을 그대로 불러 쓴다. 플러그인은 표준 라이브러리만
쓰고 이 패키지를 모르므로, 정의는 플러그인 쪽에 두고 이쪽이 따른다.

경로는 상수가 아니라 함수다 — 테스트처럼 실행 중에 `DEVDASH_HOME` 을 바꿔도 따라가게.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
"""저장소 루트. 사용자 상태가 아니라 설치된 코드(화면 빌드본 · 플러그인)를 찾을 때만 쓴다."""

PLUGIN_SCRIPTS = REPO / "plugins" / "devdash" / "scripts"
if str(PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SCRIPTS))

from devdash_store import home  # noqa: E402

__all__ = [
    "REPO",
    "PLUGIN_SCRIPTS",
    "home",
    "servers_path",
    "sessions_path",
    "log_dir",
    "claude_dir",
]


def servers_path() -> Path:
    """서버 접속 설정. 없으면 서버 0대로 뜬다."""
    return home() / "servers.yaml"


def sessions_path() -> Path:
    """관리할 Claude 세션 목록."""
    return home() / "sessions.json"


def log_dir() -> Path:
    return home() / "logs"


def claude_dir() -> Path:
    """Claude Code 설정 폴더. Claude Code 와 같이 `CLAUDE_CONFIG_DIR` 이 있으면 그곳을 쓴다."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
