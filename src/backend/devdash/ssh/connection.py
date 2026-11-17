"""서버 하나에 대한 SSH 연결 관리.

명령마다 새로 접속하면 핸드셰이크 비용이 폴링 주기를 잡아먹는다.
그래서 연결은 하나를 유지하고, 명령마다 채널만 새로 연다.
"""

from __future__ import annotations

import asyncio
import logging
import time

import asyncssh

from devdash.config import ServerConfig

logger = logging.getLogger(__name__)


def _short(command: str, limit: int = 80) -> str:
    """여러 줄짜리 배치 명령을 로그 한 줄에 담을 수 있게 줄인다."""
    single = " ".join(command.split())
    return single if len(single) <= limit else single[:limit] + "…"


class SSHCommandError(RuntimeError):
    """원격 명령이 0이 아닌 종료 코드로 끝난 경우."""

    def __init__(self, command: str, exit_status: int, stderr: str) -> None:
        self.command = command
        self.exit_status = exit_status
        self.stderr = stderr.strip()
        super().__init__(f"원격 명령 실패 (exit {exit_status}): {command}\n{self.stderr}")


class ServerConnection:
    """asyncssh 연결 하나를 재사용하는 래퍼.

    명령은 각자 다른 SSH 채널에서 **동시에** 실행된다. 하나가 오래 걸려도
    다른 명령이 막히지 않는다. docker stats 가 2초쯤 걸리는데 그동안
    5초 주기의 자원 수집이 밀리면 안 되기 때문이다.

    락은 접속과 종료에만 걸어, 여러 명령이 동시에 들어와도 연결이
    두 번 만들어지지 않게 한다.

    연결이 끊기면 다음 run() 에서 자동으로 다시 붙는다.
    재시도 간격 조절(백오프)은 이 클래스가 아니라 폴링 스케줄러가 맡는다.
    """

    def __init__(self, cfg: ServerConfig) -> None:
        self._cfg = cfg
        self._conn: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    def _secret(self) -> str | None:
        if not self._cfg.secret_from_keyring:
            return None
        import keyring  # 필요할 때만 불러온다 (OS 자격증명 저장소 접근)

        secret = keyring.get_password("devdash", self._cfg.id)
        if secret is None:
            raise RuntimeError(f"keyring 에 ('devdash', '{self._cfg.id}') 비밀 값이 없습니다.")
        return secret

    def _connect_options(self) -> dict:
        cfg = self._cfg
        opts: dict = {
            "host": cfg.host,
            "port": cfg.port,
            "username": cfg.user,
            "connect_timeout": cfg.connect_timeout,
        }

        if cfg.key_file is not None:
            opts["client_keys"] = [str(cfg.key_file)]

        secret = self._secret()
        if secret is not None:
            # 키를 쓰면 그 키의 passphrase, 아니면 계정 비밀번호로 취급한다
            opts["passphrase" if cfg.key_file else "password"] = secret

        if not cfg.verify_host_key:
            # asyncssh 는 known_hosts=None 일 때 호스트 키 검증을 하지 않는다
            opts["known_hosts"] = None
        elif cfg.known_hosts is not None:
            opts["known_hosts"] = str(cfg.known_hosts)

        return opts

    async def connect(self) -> None:
        await self._ensure_connected()

    async def _ensure_connected(self) -> asyncssh.SSHClientConnection:
        conn = self._conn
        if conn is not None:
            return conn
        async with self._lock:
            # 락을 기다리는 동안 다른 호출이 이미 붙였을 수 있다
            if self._conn is None:
                logger.info(
                    "%s: SSH 접속 시도 %s@%s:%s",
                    self._cfg.id,
                    self._cfg.user,
                    self._cfg.host,
                    self._cfg.port,
                )
                started = time.perf_counter()
                self._conn = await asyncssh.connect(**self._connect_options())
                logger.info(
                    "%s: SSH 접속됨 (%.0fms)",
                    self._cfg.id,
                    (time.perf_counter() - started) * 1000,
                )
            return self._conn

    async def run(self, command: str) -> str:
        """원격 명령을 실행하고 표준 출력을 돌려준다."""
        conn = await self._ensure_connected()
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                conn.run(command, check=False), timeout=self._cfg.command_timeout
            )
        except (TimeoutError, OSError, asyncssh.Error) as exc:
            # 끊긴 연결을 붙들고 있으면 이후 호출이 계속 실패한다
            logger.warning(
                "%s: 연결이 끊어져 버립니다 (%s) - %s",
                self._cfg.id,
                type(exc).__name__,
                _short(command),
            )
            await self._discard(conn)
            raise

        logger.debug(
            "%s: 명령 %.0fms exit=%s - %s",
            self._cfg.id,
            (time.perf_counter() - started) * 1000,
            result.exit_status,
            _short(command),
        )
        if result.exit_status != 0:
            raise SSHCommandError(command, result.exit_status or -1, str(result.stderr or ""))
        return str(result.stdout or "")

    async def _discard(self, conn: asyncssh.SSHClientConnection) -> None:
        """끊긴 연결을 버린다.

        그 사이 다른 명령이 새로 붙여둔 연결까지 닫아버리지 않도록,
        지금 들고 있는 연결이 버리려는 그 연결일 때만 치운다.
        """
        async with self._lock:
            if self._conn is not conn:
                return
            self._conn = None
        await self._close(conn)

    async def close(self) -> None:
        async with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            await self._close(conn)

    async def _close(self, conn: asyncssh.SSHClientConnection) -> None:
        logger.info("%s: SSH 연결 종료", self._cfg.id)
        conn.close()
        try:
            await conn.wait_closed()
        except Exception:  # 종료 중 예외는 무시한다
            logger.debug("%s: 연결 종료 중 예외", self._cfg.id, exc_info=True)

    async def __aenter__(self) -> ServerConnection:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
