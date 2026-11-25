"""Claude Code 버전이 바뀌면 대시보드가 스스로 호환 시험(check-claude)을 돌린다.

setup 에서 "예" 라고 한 경우만 (prefs autoCompat) — 시험은 실제 시험 세션을 띄우고
haiku 에게 두 번 묻는다.
대시보드가 뜨고 FIRST_DELAY 뒤 한 번, 그 뒤 CHECK_EVERY 마다 `claude --version` 을 보고, 마지막으로
통과한 버전과 다르면 별도 프로세스로 `devdash check-claude` 를 돌린다.
대시보드가 뜨는 것은 기다리지 않는다.

- 한 번에 하나: 손으로 돌린 check-claude 와도 겹치지 않게 compat.running() 을 본다
- 같은 버전은 자동으로 한 번만: 실패했거나 시작하지 못했으면(시험 폴더 신뢰 등)
  되풀이하지 않고 이유를 보인다.
  다시 하려면 check-claude 실행 파일로
- 마치지 못했으면(도중에 claude 실행 파일이 사라짐) RETRY_DELAY 뒤 다시, RETRIES 번까지.
  버전이 바뀐 직후에 도는 시험이라 Claude 자동 업데이트(npm 전역 설치)와 겹치기 쉽다.
  그래도 못 마치면 이유를 보인다
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time

from devdash import compat, prefs
from devdash.home import log_dir

FIRST_DELAY = 60.0
"""초. 대시보드가 뜬 직후의 일(목록 미리 읽기 · 서버 접속)과 겹치지 않게 조금 뒤에."""

CHECK_EVERY = 1800.0
"""초. 켜져 있는 동안 버전을 다시 보는 간격."""

RETRY_DELAY = 300.0
"""초. 마치지 못한 시험을 다시 하기까지 (설치가 끝날 때를 기다린다)."""

RETRIES = 2
"""마치지 못했을 때 다시 하는 횟수."""

RUN_TIMEOUT = 20 * 60
"""초. 시험이 이 안에 끝나지 않으면 이번 결과는 버린다 (보통 3~5분)."""

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(prefs.get("autoCompat"))


def should_run(version: str | None, results: dict, auto_error: dict, running: bool) -> bool:
    """이 버전을 지금 자동으로 시험할까."""
    if not version or running:
        return False
    if version == results.get("passedVersion"):
        return False  # 이미 통과한 버전
    if results.get("version") == version:
        return False  # 이 버전은 이미 시험했다 (실패) — 되풀이하지 않는다
    return auto_error.get("version") != version  # 시작하지 못한 버전도 되풀이하지 않는다


class AutoCompat:
    """대시보드 서버 안에서 도는 감시. start() 는 바로 돌아온다."""

    def __init__(self, spawn=None, sleep=time.sleep) -> None:
        self._spawn = spawn or self._run_check
        self._sleep = sleep
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None and shutil.which("claude"):
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        time.sleep(FIRST_DELAY)
        while True:
            try:
                self.check_once()
            except Exception:
                logger.exception("자동 호환 시험을 확인하지 못했다")
            time.sleep(CHECK_EVERY)

    def check_once(self) -> bool:
        """지금 시험할 차례면 돌리고(끝날 때까지 기다림) True."""
        if not enabled():
            return False
        version = compat.installed_version(fresh=True)
        if not should_run(
            version, compat.load_results(), compat.load_auto_error(), compat.running() is not None
        ):
            return False
        reason = None
        for attempt in range(RETRIES + 1):
            if attempt:
                logger.info("%s — %.0f초 뒤 다시 (%d/%d)", reason, RETRY_DELAY, attempt, RETRIES)
                self._sleep(RETRY_DELAY)
                # 그사이 버전이 또 바뀌었으면 새 버전으로. 아직 못 읽으면(설치 중) 그대로 해 본다
                version = compat.installed_version(fresh=True) or version
                if not enabled() or not should_run(
                    version,
                    compat.load_results(),
                    compat.load_auto_error(),
                    compat.running() is not None,
                ):
                    return True  # 사람이 그사이 시험했거나 하고 있다
            logger.info("Claude Code %s — 자동 호환 시험을 시작한다", version)
            reason = self._spawn(version)
            if reason is None:
                return True
        compat.save_auto_error(version, f"{RETRIES + 1}번 해 봤지만 마치지 못했습니다 — {reason}")
        return True

    @staticmethod
    def _run_check(version: str) -> str | None:
        """별도 프로세스로 check-claude 를 돌린다. 마쳤으면(통과 · 실패) None, 마치지 못했으면
        (도중에 claude 가 사라짐) 이유. 시작하지도 못했으면 이유를 남겨 화면이 보이게 하고 None."""
        log = log_dir() / "compat-auto.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        before = compat.load_results().get("at"), compat.load_results().get("version")
        with open(log, "w", encoding="utf-8", errors="replace") as out:
            try:
                code = subprocess.run(
                    [sys.executable, "-m", "devdash", "check-claude"],
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    timeout=RUN_TIMEOUT,
                    **kwargs,
                ).returncode
            except subprocess.TimeoutExpired:
                code = -1
        results = compat.load_results()
        if code == compat.UNFINISHED:
            return (results.get("unfinished") or {}).get("reason") or "마치지 못함"
        if (results.get("at"), results.get("version")) == before:
            text = log.read_text(encoding="utf-8", errors="replace").strip()
            if code == -1:
                reason = f"시험이 {RUN_TIMEOUT // 60}분 안에 끝나지 않았습니다"
            else:
                reason = text.splitlines()[0] if text else f"check-claude 실패 (코드 {code})"
            compat.save_auto_error(version, reason, text)
        return None


def save_preference(value: bool) -> None:
    prefs.put("autoCompat", bool(value))
