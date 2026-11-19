"""이 PC 에 붙는 동작.

- 메모리 · 앱별 메모리 · 프로세스 트리 끄기 · 시간 제한 실행: OS 공통 (system.py, psutil)
- 터미널 창 열기 · 폴더 선택 창: 윈도우만 (windows.py). 다른 OS 에서는 부르면 OSError 이고,
  화면은 ABILITIES 를 보고 버튼을 숨긴다
"""

from __future__ import annotations

import sys

from devdash.local.system import app_memory, kill_tree, memory_status, run_bounded

if sys.platform == "win32":
    from devdash.local.windows import open_terminal, pick_folder
else:

    def open_terminal(cwd: str, title: str, args: list[str]) -> None:
        raise OSError("터미널 창 열기는 윈도우에서만 됩니다")

    def pick_folder() -> str | None:
        raise OSError("폴더 선택 창은 윈도우에서만 됩니다. 경로를 직접 적어 주세요")


ABILITIES = {"terminal": sys.platform == "win32", "pickFolder": sys.platform == "win32"}
"""이 OS 에서 되는 것 — 화면이 안 되는 버튼을 숨긴다."""

__all__ = [
    "ABILITIES",
    "app_memory",
    "kill_tree",
    "memory_status",
    "open_terminal",
    "pick_folder",
    "run_bounded",
]
