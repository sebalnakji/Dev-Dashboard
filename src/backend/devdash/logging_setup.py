"""로그 설정.

모니터링 도구의 로그는 사후 추적이 목적이라 두 가지가 반드시 필요하다.

- **시각** — 언제 끊겼고 언제 돌아왔는지 알 수 없으면 로그가 소용없다
- **파일** — 웹 서버를 백그라운드로 띄우면 콘솔 출력은 그대로 사라진다

콘솔은 조용하게(INFO) 두고 파일에는 전부(DEBUG) 남긴다. 그래야 평소 화면은
깨끗하면서도 문제가 생겼을 때 되짚어볼 기록이 남는다.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from devdash.home import log_dir as default_log_dir

LOG_FILENAME = "devdash.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5
"""5MB 씩 5개까지 보관. 서버 두 대를 5초 주기로 돌리면 대략 일주일치다."""

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(verbose: bool = False, log_dir: Path | None = None) -> None:
    """콘솔과 파일에 로그를 남기도록 설정한다.

    파일을 만들 수 없는 환경(권한 없음 등)에서는 콘솔만 쓴다.
    로그를 못 남긴다고 모니터링 자체가 멈출 이유는 없다.
    """
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    # asyncssh 는 INFO 에서도 채널을 열고 닫을 때마다 여러 줄을 남긴다.
    # 접속과 명령 실행은 우리가 직접 기록하므로 평소에는 경고만 받는다.
    # -v 를 주면 INFO 까지 열어 남의 라이브러리 관점도 같이 본다(DEBUG 는 패킷 단위라 제외).
    library_level = logging.INFO if verbose else logging.WARNING
    for name in ("asyncssh", "asyncio"):
        logging.getLogger(name).setLevel(library_level)

    directory = default_log_dir() if log_dir is None else log_dir
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / LOG_FILENAME
        file_handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except OSError as exc:
        root.warning("로그 파일을 만들지 못해 콘솔에만 기록합니다: %s", exc)
        return

    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root.info("로그 파일: %s", path)
