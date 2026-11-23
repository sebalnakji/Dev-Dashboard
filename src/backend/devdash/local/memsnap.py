"""앱별 메모리를 별도 프로세스에서 센다 — 대시보드 서버를 붙잡지 않게.

프로세스 수백 개를 psutil 로 훑는 일은 따로 돌리면 0.7초인데, 서버 안에서 돌리면 다른 스레드와
GIL 을 다퉈 3~5초가 걸리고 그동안 다른 요청까지 밀렸다 (서버 목록 0.005초 → 1초).
그래서 세기는 자식 프로세스(`python -m devdash.local.memsnap`)가 하고 결과만 JSON 으로 넘긴다.
서버는 뒤에서 INTERVAL 마다 돌려 마지막 값을 들고 있고, 요청에는 그 값을 바로 준다.
아무도 안 본 지 IDLE_STOP 이 지나면 세기를 멈춘다.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time

INTERVAL = 10.0
"""초. 앱별 메모리를 다시 세는 간격 (화면도 같은 간격으로 묻는다)."""

IDLE_STOP = 60.0
"""초. 이만큼 아무도 묻지 않으면 세기를 멈춘다 (화면을 닫았거나 다른 탭)."""

TIMEOUT = 30.0
"""초. 자식 프로세스가 이 안에 끝나지 않으면 이번 값은 버린다."""

logger = logging.getLogger(__name__)


def ranked_apps(top: int) -> list[dict]:
    """[{"name", "bytes"}] 많이 쓰는 앱부터 top 개. 자식 프로세스 안에서 돈다."""
    from devdash.local.system import app_memory

    ranked = sorted(app_memory().items(), key=lambda kv: kv[1], reverse=True)
    return [{"name": n, "bytes": b} for n, b in ranked[:top]]


class MemoryWatcher:
    """앱별 메모리의 마지막 값을 들고 있고, 누가 보는 동안 뒤에서 INTERVAL 마다 새로 센다."""

    def __init__(self, top: int, runner=None) -> None:
        self.top = top
        self._run = runner or self._run_child
        self._apps: list[dict] | None = None
        self._asked = 0.0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def apps(self) -> list[dict] | None:
        """마지막으로 센 앱 목록 (아직 한 번도 못 셌으면 None). 묻는 것만으로 세기가 이어진다."""
        with self._lock:
            self._asked = time.time()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()
            return self._apps

    def _loop(self) -> None:
        while time.time() - self._asked <= IDLE_STOP:
            try:
                apps = self._run(self.top)
                if apps is not None:
                    with self._lock:
                        self._apps = apps
            except Exception:
                logger.exception("앱별 메모리를 세지 못했다")
            time.sleep(INTERVAL)

    @staticmethod
    def _run_child(top: int) -> list[dict] | None:
        from devdash.local import run_bounded

        out = run_bounded(
            [sys.executable, "-m", "devdash.local.memsnap", str(top)], timeout=TIMEOUT
        )
        if out is None or out[0] != 0:
            logger.warning("앱별 메모리 프로세스가 실패했다: %s", out and out[2][-300:])
            return None
        return json.loads(out[1])


if __name__ == "__main__":
    print(json.dumps(ranked_apps(int(sys.argv[1]) if len(sys.argv) > 1 else 10)))
