"""서버별 주기 수집.

서버마다 SSH 연결 하나를 잡고, 자원 지표와 Docker 상태를 각자의 주기로 반복 수집한다.
두 수집은 서로 다른 SSH 채널에서 돌기 때문에, 2초쯤 걸리는 docker stats 가
5초 주기의 자원 수집을 밀어내지 않는다.

한 서버가 죽어도 다른 서버 수집은 계속돼야 하므로 서버마다 독립된 태스크로 띄운다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from devdash.collect.docker import DockerCollector
from devdash.collect.system import SystemCollector
from devdash.config import AppConfig, ServerConfig
from devdash.ssh.connection import ServerConnection
from devdash.store.base import MetricStore

logger = logging.getLogger(__name__)

BACKOFF_START = 1.0
BACKOFF_MAX = 60.0
"""연결이 끊겼을 때 재시도 간격. 1초에서 시작해 두 배씩, 60초까지 늘린다."""


@dataclass
class ServerState:
    """수집이 지금 어떤 상태인지. 화면의 연결 배지가 이 값을 쓴다."""

    status: str = "connecting"
    """connecting | online | error"""

    error: str | None = None
    """SSH 연결이나 자원 수집이 실패한 이유."""

    docker_error: str | None = None
    """SSH 는 멀쩡한데 docker 명령만 실패한 경우. 권한 문제가 대표적이다."""


def _describe(exc: BaseException) -> str:
    """예외를 한 줄로. 메시지가 없는 예외도 있어 그때는 종류만 남긴다.

    TimeoutError 처럼 본문이 빈 예외를 그대로 쓰면 "TimeoutError: " 가 되어
    로그에도 화면에도 쓸모가 없다.
    """
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class ServerPoller:
    """서버 하나를 맡아 수집을 반복한다."""

    def __init__(self, cfg: ServerConfig, store: MetricStore) -> None:
        self._cfg = cfg
        self._store = store
        self._conn = ServerConnection(cfg)
        self._system = SystemCollector()
        self._docker = DockerCollector(cfg)
        self.state = ServerState()

    @property
    def connection(self) -> ServerConnection:
        """이미 열려 있는 연결. 로그 조회처럼 그때그때 필요한 명령이 함께 쓴다."""
        return self._conn

    async def run(self) -> None:
        try:
            await asyncio.gather(
                self._repeat(self._cfg.poll.system, self._system_step),
                self._repeat(self._cfg.poll.docker, self._docker_step),
            )
        finally:
            await self._conn.close()

    async def _repeat(self, period: float, step: Callable[[], Awaitable[None]]) -> None:
        """step 을 주기적으로 부른다. 실패하면 간격을 늘려가며 다시 시도한다."""
        backoff = BACKOFF_START
        while True:
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "%s: 수집 실패, %.0f초 후 다시 시도 - %s",
                    self._cfg.id,
                    backoff,
                    _describe(exc),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            backoff = BACKOFF_START
            await asyncio.sleep(period)

    async def _system_step(self) -> None:
        started = time.perf_counter()
        try:
            sample = await self._system.collect(self._conn)
        except Exception as exc:
            self.state.status = "error"
            self.state.error = _describe(exc)
            raise
        self._store.add_sample(self._cfg.id, sample)

        if self.state.status == "connecting":
            logger.info("%s: 자원 수집 시작", self._cfg.id)
        elif self.state.status == "error":
            logger.info("%s: 자원 수집 복구됨", self._cfg.id)
        self.state.status = "online"
        self.state.error = None
        logger.debug("%s: 자원 수집 %.0fms", self._cfg.id, (time.perf_counter() - started) * 1000)

    async def _docker_step(self) -> None:
        # docker 실패는 SSH 연결 상태와 별개다. 권한이 없어도 자원 수집은 계속돼야 한다.
        started = time.perf_counter()
        try:
            snapshot = await self._docker.collect(self._conn)
        except Exception as exc:
            self.state.docker_error = _describe(exc)
            raise
        self._store.set_docker(self._cfg.id, snapshot)

        if self.state.docker_error is not None:
            logger.info("%s: docker 수집 복구됨", self._cfg.id)
        self.state.docker_error = None
        logger.debug(
            "%s: docker 수집 %.0fms, 컨테이너 %d개(실행 %d개)",
            self._cfg.id,
            (time.perf_counter() - started) * 1000,
            len(snapshot.containers),
            snapshot.running_count,
        )


class Scheduler:
    """설정에 있는 모든 서버의 수집 태스크를 띄우고 거둔다."""

    def __init__(self, config: AppConfig, store: MetricStore) -> None:
        self._pollers = {cfg.id: ServerPoller(cfg, store) for cfg in config.servers}
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        logger.info("수집 시작: 서버 %d대 (%s)", len(self._pollers), ", ".join(self._pollers))
        self._tasks = [
            asyncio.create_task(poller.run(), name=f"poll:{server_id}")
            for server_id, poller in self._pollers.items()
        ]

    async def stop(self) -> None:
        logger.info("수집 중지")
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def states(self) -> dict[str, ServerState]:
        return {server_id: poller.state for server_id, poller in self._pollers.items()}

    def connection(self, server_id: str) -> ServerConnection:
        """수집이 쓰는 연결을 그대로 빌려준다.

        요청마다 새로 접속하면 핸드셰이크에 100ms 넘게 든다. SSH 는 한 연결에
        채널을 여러 개 열 수 있으므로 수집을 방해하지 않는다.
        """
        return self._pollers[server_id].connection
