"""이 PC 에 붙는 동작. 지금은 윈도우만 지원한다 (ADR 0005).

다른 OS 를 지원할 때는 windows.py 와 같은 함수를 가진 파일을 만들고 여기서 고른다.
"""

from devdash.local.windows import (
    app_memory,
    kill_tree,
    memory_status,
    open_terminal,
    pick_folder,
    run_bounded,
)

__all__ = [
    "app_memory",
    "kill_tree",
    "memory_status",
    "open_terminal",
    "pick_folder",
    "run_bounded",
]
