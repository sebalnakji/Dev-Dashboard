"""콘솔 출력과 진단 명령.

웹 대시보드가 있어도 서버를 새로 등록할 때는 콘솔에서 먼저 확인하는 편이 빠르다.
접속이 안 될 때 무엇을 고쳐야 하는지 짚어주는 안내도 여기에 있다.
"""

from __future__ import annotations

import asyncio
import sys
import time
import unicodedata
from pathlib import Path

from devdash.collect.docker import DockerCollector
from devdash.collect.system import SystemCollector
from devdash.config import ServerConfig, load_config
from devdash.models import DockerSnapshot, SystemSample
from devdash.ssh.connection import ServerConnection, SSHCommandError


def display_width(text: str) -> int:
    """터미널에서 차지하는 칸 수. 한글은 두 칸을 쓴다."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, width: int) -> str:
    """표시 폭 기준으로 오른쪽을 채운다.

    f-string 의 :<16 은 글자 수로 세기 때문에 한글이 섞이면 열이 어긋난다.
    """
    return text + " " * max(0, width - display_width(text))


def use_utf8_when_redirected() -> None:
    """출력을 파이프나 파일로 넘길 때 한글이 깨지지 않게 한다.

    콘솔에 직접 쓸 때는 파이썬이 유니코드를 그대로 넘기므로 건드리지 않는다.
    리다이렉트되면 로캘 인코딩(한국어 윈도우는 cp949)이 쓰여 문자가 깨진다.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")


def format_bytes(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TiB"


def format_duration(seconds: float) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}일 {hours}시간"
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분"


def _percent(value: float | None) -> str:
    return "측정 중" if value is None else f"{value:.1f} %"


def _speed(value: float | None) -> str:
    return "측정 중" if value is None else f"{format_bytes(value)}/s"


def print_sample(cfg: ServerConfig, sample: SystemSample, elapsed_ms: float) -> None:
    print(f"\n{cfg.display_name}  ({cfg.user}@{cfg.host}:{cfg.port})")
    print("-" * 60)
    print(f"  CPU       {_percent(sample.cpu_percent)}")
    print(
        f"  메모리    {format_bytes(sample.mem_used_bytes)} / "
        f"{format_bytes(sample.mem_total_bytes)}  ({sample.mem_percent:.1f} %)"
    )
    if sample.swap_total_bytes:
        print(
            f"  스왑      {format_bytes(sample.swap_used_bytes)} / "
            f"{format_bytes(sample.swap_total_bytes)}"
        )
    print(f"  로드      {sample.load1:.2f} / {sample.load5:.2f} / {sample.load15:.2f}")
    print(f"  가동      {format_duration(sample.uptime_seconds)}")
    print(f"  네트워크  ↓ {_speed(sample.net_rx_bps)}   ↑ {_speed(sample.net_tx_bps)}")

    for gpu in sample.gpus:
        print(
            f"  GPU {gpu.index}     {_percent(gpu.utilization_percent)}"
            f"   VRAM {format_bytes(gpu.memory_used_bytes)} / "
            f"{format_bytes(gpu.memory_total_bytes)} ({gpu.memory_percent:.1f} %)"
            f"   {gpu.name}"
        )

    if sample.disks:
        print("\n  디스크")
        width = max(display_width(d.mount) for d in sample.disks)
        for disk in sample.disks:
            print(
                f"    {pad(disk.mount, width)}  {disk.percent:5.1f} %  "
                f"{format_bytes(disk.used_bytes)} / {format_bytes(disk.total_bytes)}"
                f"  ({disk.fstype})"
            )

    print(f"\n  수집 왕복  {elapsed_ms:.0f} ms")


def _container_sort_key(container) -> tuple:
    """실행 중인 것을 먼저, 그 안에서는 CPU 를 많이 쓰는 순으로."""
    cpu = -1.0
    if container.stats and container.stats.cpu_percent is not None:
        cpu = container.stats.cpu_percent
    return (0 if container.running else 1, -cpu, container.name)


def print_containers(result: DockerSnapshot | str) -> None:
    if isinstance(result, str):
        print(f"\n  컨테이너  가져오지 못함: {result}")
        return

    print(f"\n  컨테이너  실행 {result.running_count} / 중지 {result.stopped_count}")
    if not result.containers:
        return

    rows = sorted(result.containers, key=_container_sort_key)
    name_w = max(display_width(c.name) for c in rows)
    # "Up 2 days (healthy)" 처럼 뒤에 붙는 헬스체크 상태가 잘리지 않을 만큼은 확보한다
    status_w = min(30, max(display_width(c.status) for c in rows))

    for c in rows:
        cpu = "-"
        mem = "-"
        if c.stats:
            if c.stats.cpu_percent is not None:
                cpu = f"{c.stats.cpu_percent:.1f} %"
            if c.stats.mem_used_bytes is not None:
                mem = format_bytes(c.stats.mem_used_bytes)
        status = c.status if display_width(c.status) <= status_w else c.status[: status_w - 1] + "…"
        mark = "●" if c.running else "○"
        print(f"    {mark} {pad(c.name, name_w)}  {pad(status, status_w)}  {cpu:>7}  {mem:>11}")


async def _collect_docker(
    collector: DockerCollector, conn: ServerConnection
) -> DockerSnapshot | str:
    """실패하면 사람이 읽을 문구를 돌려준다.

    docker 가 없거나 권한이 없는 서버여도 자원 지표 확인은 계속되어야 한다.
    """
    try:
        return await collector.collect(conn)
    except SSHCommandError as exc:
        first_line = exc.stderr.splitlines()[0] if exc.stderr else ""
        return first_line or f"docker 명령이 실패했습니다 (exit {exc.exit_status})"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


async def _check(
    cfg: ServerConfig, interval: float
) -> tuple[SystemSample, DockerSnapshot | str, float]:
    """지표를 두 번 수집한다.

    CPU 사용률과 네트워크 속도는 누적값의 차이로 계산되므로
    한 번만 수집해서는 값이 나오지 않는다.

    두 번째 수집에서 Docker 도 같이 가져온다. 서로 다른 SSH 채널에서 돌기 때문에
    2초쯤 걸리는 docker stats 가 자원 지표 수집을 붙들지 않는다.
    """
    collector = SystemCollector()
    docker = DockerCollector(cfg)
    async with ServerConnection(cfg) as conn:
        await collector.collect(conn)
        await asyncio.sleep(interval)

        started = time.perf_counter()
        sample, snapshot = await asyncio.gather(
            collector.collect(conn), _collect_docker(docker, conn)
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
    return sample, snapshot, elapsed_ms


def cmd_list(config_path: Path | None) -> int:
    config = load_config(config_path)
    print(f"{pad('ID', 16)} {pad('이름', 18)} {pad('접속', 28)} docker sudo")
    print("-" * 67)
    for s in config.servers:
        target = f"{s.user}@{s.host}:{s.port}"
        print(
            f"{pad(s.id, 16)} {pad(s.display_name, 18)} {pad(target, 28)} "
            f"{'예' if s.docker_sudo else '아니오'}"
        )
    return 0


def connection_hint(exc: BaseException, cfg: ServerConfig | None) -> str | None:
    """접속 실패 원인을 짚어주는 안내문.

    asyncssh 의 원문 메시지만으로는 무엇을 고쳐야 할지 알기 어렵다.
    처음 서버를 등록할 때 실제로 자주 걸리는 것들만 추린다.
    """
    import asyncssh

    target = f"{cfg.host}:{cfg.port}" if cfg else "서버"

    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return (
            f"이 서버의 호스트 키가 known_hosts 에 없습니다.\n"
            f"  터미널에서 한 번 직접 접속해 등록하세요:  ssh {cfg.user}@{cfg.host}\n"
            f"  또는 설정에서 verify_host_key: false 로 검증을 끌 수 있습니다"
            f" (신뢰하는 내부망에서만)."
        )
    if isinstance(exc, asyncssh.PermissionDenied):
        return (
            "인증이 거부되었습니다.\n"
            "  key_file 경로가 맞는지, 그 키가 서버에 등록되어 있는지 확인하세요.\n"
            "  키에 passphrase 가 걸려 있으면 secret_from_keyring: true 로 두고"
            " keyring 에 넣어야 합니다."
        )
    if isinstance(exc, asyncssh.KeyImportError):
        return "키 파일을 읽지 못했습니다. 경로와 형식을 확인하세요."
    if isinstance(exc, asyncio.TimeoutError):
        return f"{target} 응답이 없습니다. 주소·포트·방화벽(또는 VPN 연결)을 확인하세요."
    if isinstance(exc, ConnectionRefusedError):
        return f"{target} 에서 연결을 거부했습니다. SSH 포트가 맞는지 확인하세요."
    if isinstance(exc, OSError) and "getaddrinfo" in str(exc):
        return "호스트 이름을 찾지 못했습니다. host 값을 확인하세요."
    return None


async def _check_many(
    cfgs: list[ServerConfig], interval: float
) -> list[tuple[ServerConfig, object]]:
    """여러 서버를 동시에 확인한다.

    한 서버가 실패해도 나머지는 계속 진행해야 하므로 예외를 값으로 받는다.
    """
    results = await asyncio.gather(*(_check(cfg, interval) for cfg in cfgs), return_exceptions=True)
    # gather 는 입력 순서대로 같은 개수를 돌려주므로 길이가 어긋나면 그게 버그다
    return list(zip(cfgs, results, strict=True))


def cmd_check(config_path: Path | None, server_ids: list[str], interval: float) -> int:
    config = load_config(config_path)
    targets = [config.get(sid) for sid in server_ids] if server_ids else config.servers
    if not targets:
        print("설정에 서버가 없습니다.", file=sys.stderr)
        return 1

    names = ", ".join(c.display_name for c in targets)
    print(f"{len(targets)}개 서버에 동시 접속합니다: {names}  (샘플 간격 {interval:g}초)")

    started = time.perf_counter()
    results = asyncio.run(_check_many(targets, interval))
    total_ms = (time.perf_counter() - started) * 1000

    failed = 0
    for cfg, result in results:
        if isinstance(result, BaseException):
            failed += 1
            print(f"\n{cfg.display_name}  ({cfg.user}@{cfg.host}:{cfg.port})")
            print("-" * 60)
            print(f"  실패: {type(result).__name__}: {result}")
            hint = connection_hint(result, cfg)
            if hint:
                print("\n  " + hint.replace("\n", "\n  "))
        else:
            sample, snapshot, elapsed_ms = result
            print_sample(cfg, sample, elapsed_ms)
            print_containers(snapshot)

    ok = len(results) - failed
    print(f"\n{'=' * 60}")
    print(f"성공 {ok} / 실패 {failed}   전체 소요 {total_ms / 1000:.1f}초")
    return 1 if failed else 0
