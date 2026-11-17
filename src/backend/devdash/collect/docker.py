"""Docker 컨테이너 수집.

`docker ps` 와 `docker stats` 를 실행해 결과를 파싱한다.

두 명령의 성격이 아주 다르다. 실서버에서 재보면 ps 는 30ms 인데
stats 는 컨테이너 수와 무관하게 2.0~2.6초가 걸린다. Docker 가 CPU 사용률을
내려고 내부적으로 일정 시간 샘플링하기 때문이다. 그래서 둘의 수집 주기를
분리하고, 목록은 자주 사용량은 가끔 갱신한다.

`{{json .}}` 대신 필요한 필드만 뽑는다. 그대로 쓰면 compose 라벨이
컨테이너당 1KB 씩 붙어 나온다. 필드마다 json 함수를 씌우면 따옴표와
이스케이프는 Docker 가 알아서 처리해 준다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import replace

from devdash.config import ServerConfig
from devdash.models import Container, ContainerStats, DockerSnapshot

logger = logging.getLogger(__name__)

_LIST_FIELDS = (
    '{"id":{{json .ID}},"name":{{json .Names}},"image":{{json .Image}},'
    '"state":{{json .State}},"status":{{json .Status}},"ports":{{json .Ports}}}'
)

_STATS_FIELDS = '{"id":{{json .ID}},"cpu":{{json .CPUPerc}},"mem":{{json .MemUsage}}}'


def list_command(sudo: bool = False) -> str:
    """중지된 것까지 포함해 컨테이너 목록을 가져온다(-a).

    죽은 줄 몰랐던 컨테이너를 발견하는 게 모니터링의 목적이라 전체를 본다.
    """
    prefix = "sudo -n " if sudo else ""
    return f"{prefix}docker ps -a --format '{_LIST_FIELDS}'"


def logs_command(container_id: str, tail: int, sudo: bool = False) -> str:
    """컨테이너 로그 마지막 몇 줄.

    이름이 아니라 id 로 지정한다. id 는 16진수뿐이라 셸에 그대로 넘겨도 안전하고,
    이름이 바뀌거나 겹치는 경우에도 흔들리지 않는다. 호출부는 넘기기 전에
    수집된 목록에 있는 id 인지 확인해야 한다.

    2>&1 로 합치는 이유는, 컨테이너가 표준 오류로 찍은 줄도 로그의 일부이기 때문이다.
    """
    prefix = "sudo -n " if sudo else ""
    return f"{prefix}docker logs --tail {int(tail)} {container_id} 2>&1"


def stats_command(sudo: bool = False) -> str:
    prefix = "sudo -n " if sudo else ""
    return f"{prefix}docker stats --no-stream --format '{_STATS_FIELDS}'"


# Docker 는 메모리에 이진 단위(MiB), 네트워크와 블록 IO 에 십진 단위(MB)를 쓴다.
_UNIT_FACTORS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
}

_SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([a-zA-Z]*)\s*$")


def parse_size(text: str | None) -> int | None:
    """ "39.24MiB", "3.95MB", "0B" 을 바이트로 바꾼다.

    값이 없으면 Docker 가 "--" 를 내보내는데, 그때는 None 이다.
    """
    if not text:
        return None
    match = _SIZE_RE.match(text)
    if match is None:
        return None
    amount, unit = match.groups()
    factor = _UNIT_FACTORS.get(unit.lower() or "b")
    if factor is None:
        return None
    try:
        return int(float(amount) * factor)
    except ValueError:
        return None


def parse_size_pair(text: str | None) -> tuple[int | None, int | None]:
    """ "39.24MiB / 62.44GiB" 처럼 슬래시로 묶인 두 값을 나눈다."""
    if not text or "/" not in text:
        return None, None
    left, _, right = text.partition("/")
    return parse_size(left), parse_size(right)


def parse_percent(text: str | None) -> float | None:
    """ "0.06%" 를 실수로 바꾼다."""
    if not text:
        return None
    try:
        return float(text.strip().rstrip("%"))
    except ValueError:
        return None


def _iter_json_lines(output: str) -> list[dict]:
    rows: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # 한 줄이 깨져도 나머지는 살린다
            logger.warning("docker 출력 한 줄을 해석하지 못했습니다: %r", line[:200])
    return rows


def parse_container_list(output: str) -> tuple[Container, ...]:
    return tuple(
        Container(
            id=row.get("id", ""),
            name=row.get("name", ""),
            image=row.get("image", ""),
            state=row.get("state", ""),
            status=row.get("status", ""),
            ports=row.get("ports", ""),
        )
        for row in _iter_json_lines(output)
    )


def parse_stats(output: str) -> dict[str, ContainerStats]:
    """컨테이너 id 로 찾을 수 있게 사용량을 모은다."""
    stats: dict[str, ContainerStats] = {}
    for row in _iter_json_lines(output):
        container_id = row.get("id", "")
        if not container_id:
            continue
        # "39.24MiB / 62.44GiB" 에서 앞쪽만 쓴다. 한계값은 mem_perc 에 이미 반영돼 있다
        mem_used, _limit = parse_size_pair(row.get("mem"))
        stats[container_id] = ContainerStats(
            cpu_percent=parse_percent(row.get("cpu")),
            mem_used_bytes=mem_used,
        )
    return stats


def merge_stats(
    containers: tuple[Container, ...], stats: dict[str, ContainerStats]
) -> tuple[Container, ...]:
    """목록에 사용량을 붙인다.

    stats 는 목록보다 드물게 갱신되므로, 두 결과의 컨테이너가 서로 다를 수 있다.
    목록에 없는 stats 는 버리고, stats 가 없는 컨테이너는 None 으로 남긴다.
    """
    return tuple(replace(c, stats=stats[c.id]) if c.id in stats else c for c in containers)


class DockerCollector:
    """서버 하나의 컨테이너 상태를 수집한다.

    목록과 사용량의 수집 주기가 다르므로, 마지막으로 받은 사용량을 들고 있다가
    새 목록에 붙여준다. 그래야 목록만 갱신된 사이에도 화면에서 사용량이
    사라지지 않는다.
    """

    def __init__(self, cfg: ServerConfig) -> None:
        self._sudo = cfg.docker_sudo
        self._last_stats: dict[str, ContainerStats] = {}

    async def collect_list(self, conn) -> tuple[Container, ...]:
        output = await conn.run(list_command(self._sudo))
        return parse_container_list(output)

    async def collect_stats(self, conn) -> dict[str, ContainerStats]:
        output = await conn.run(stats_command(self._sudo))
        self._last_stats = parse_stats(output)
        return self._last_stats

    async def collect(self, conn) -> DockerSnapshot:
        """목록과 사용량을 함께 가져온다.

        둘은 각자 다른 SSH 채널에서 동시에 실행되므로, 2초 걸리는 stats 가
        목록 조회를 붙들지 않는다.
        """
        containers, _ = await asyncio.gather(self.collect_list(conn), self.collect_stats(conn))
        return DockerSnapshot(ts=time.time(), containers=merge_stats(containers, self._last_stats))
