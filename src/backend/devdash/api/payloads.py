"""수집 결과를 화면이 쓰는 모양으로 바꾼다.

내부 모델을 그대로 내보내지 않는 이유는 두 가지다. 화면에서 안 쓰는 값(누적
카운터 같은 것)까지 나가지 않게 하고, 모델을 고쳐도 화면 계약이 흔들리지 않게
하기 위해서다.

HTTP 없이 검증할 수 있도록 전부 순수 함수로 둔다.
"""

from __future__ import annotations

from devdash.collect.scheduler import ServerState
from devdash.config import ServerConfig
from devdash.models import Container, DockerSnapshot, GpuInfo, SystemSample

DEFAULT_CHART_POINTS = 600
"""차트에 보낼 기본 점 개수. 화면 폭보다 촘촘한 점은 그려도 보이지 않는다."""

SPARKLINE_POINTS = 60
"""카드 스파크라인에 쓸 최근 샘플 수."""


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _int(value: float | None) -> int | None:
    """바이트/초처럼 소수점이 의미 없는 값. round(x, 0) 은 1024.0 을 내놓는다."""
    return None if value is None else int(value)


def gpu_payload(gpu: GpuInfo) -> dict:
    """차트의 한 점. 이름과 절대값은 점마다 반복될 필요가 없어 빼둔다."""
    return {
        "index": gpu.index,
        "utilization_percent": _round(gpu.utilization_percent),
        "memory_percent": _round(gpu.memory_percent),
    }


def gpu_summary(gpu: GpuInfo) -> dict:
    """카드용. 모델명과 실제 용량까지 담는다."""
    return {
        **gpu_payload(gpu),
        "name": gpu.name,
        "memory_used_bytes": gpu.memory_used_bytes,
        "memory_total_bytes": gpu.memory_total_bytes,
    }


def busiest_disk(sample: SystemSample) -> dict | None:
    """카드에는 가장 많이 찬 파티션 하나만 보여준다.

    위험한 것이 저절로 올라오고, /data 같은 파티션이 나중에 붙어도 그대로 동작한다.
    """
    if not sample.disks:
        return None
    disk = max(sample.disks, key=lambda d: d.percent)
    return {
        "mount": disk.mount,
        "percent": _round(disk.percent),
        "used_bytes": disk.used_bytes,
        # 퍼센트와 맞아떨어지도록, 예약 블록을 뺀 값을 전체로 본다
        "total_bytes": disk.used_bytes + disk.available_bytes,
    }


def server_summary(
    cfg: ServerConfig,
    state: ServerState,
    sample: SystemSample | None,
    docker: DockerSnapshot | None,
    spark: list[float | None] | None = None,
) -> dict:
    """그리드 카드 한 장이 그리는 값. 카드에 없는 것은 담지 않는다."""
    payload: dict = {
        "id": cfg.id,
        "label": cfg.display_name,
        "status": state.status,
        "error": state.error,
        "docker_error": state.docker_error,
        "ts": sample.ts if sample else None,
        "cpu_percent": _round(sample.cpu_percent) if sample else None,
        "cpu_model": sample.cpu_model if sample else None,
        "cpu_cores": sample.cpu_cores if sample else None,
        "mem_percent": _round(sample.mem_percent) if sample else None,
        "mem_used_bytes": sample.mem_used_bytes if sample else None,
        "mem_total_bytes": sample.mem_total_bytes if sample else None,
        "disk": busiest_disk(sample) if sample else None,
        "gpus": [gpu_summary(g) for g in sample.gpus] if sample else [],
        "containers": (
            {"running": docker.running_count, "stopped": docker.stopped_count} if docker else None
        ),
    }
    if spark is not None:
        payload["cpu_spark"] = spark
    return payload


def sample_point(sample: SystemSample) -> dict:
    """상세 뷰 차트의 한 점.

    메모리는 퍼센트와 절대값을 함께 보낸다. 그래프는 퍼센트로 그리고
    마우스를 올리면 실제 용량을 보여주기 위해서다.
    """
    return {
        "ts": sample.ts,
        "cpu_percent": _round(sample.cpu_percent),
        "mem_percent": _round(sample.mem_percent),
        "mem_used_bytes": sample.mem_used_bytes,
        "disks": [{"mount": d.mount, "percent": _round(d.percent)} for d in sample.disks],
        "net_rx_bps": _int(sample.net_rx_bps),
        "net_tx_bps": _int(sample.net_tx_bps),
        "gpus": [gpu_payload(g) for g in sample.gpus],
    }


def downsample(samples: tuple[SystemSample, ...], max_points: int) -> list[SystemSample]:
    """차트에 그릴 만큼만 골라낸다.

    구간을 나눠 각 구간에서 CPU 가 가장 높았던 샘플을 고른다. 일정 간격으로
    건너뛰면 한 샘플짜리 스파이크가 통째로 사라지는데, 스파이크가 언제 있었는지
    보는 것이 이 그래프의 주목적이라 그건 곤란하다.

    고른 점은 모두 실제로 수집된 샘플이라 없는 값을 지어내지 않는다.
    마지막 점은 항상 가장 최근 샘플로 맞춘다. 그래프의 오른쪽 끝이
    현재 상태여야 하기 때문이다.
    """
    if max_points <= 0 or len(samples) <= max_points:
        return list(samples)

    step = len(samples) / max_points
    picked = []
    for i in range(max_points):
        start = int(i * step)
        end = len(samples) if i == max_points - 1 else int((i + 1) * step)
        bucket = samples[start:end]
        picked.append(max(bucket, key=lambda s: s.cpu_percent or -1.0))
    picked[-1] = samples[-1]
    return picked


def samples_payload(server_id: str, samples: tuple[SystemSample, ...], max_points: int) -> dict:
    """전체 용량처럼 매 점마다 같은 값은 봉투에 한 번만 담는다."""
    picked = downsample(samples, max_points)
    return {
        "server_id": server_id,
        "mem_total_bytes": picked[-1].mem_total_bytes if picked else None,
        "samples": [sample_point(s) for s in picked],
    }


def container_row(container: Container) -> dict:
    stats = container.stats
    return {
        "id": container.id,
        "name": container.name,
        "image": container.image,
        "state": container.state,
        "status": container.status,
        "ports": container.ports,
        "cpu_percent": _round(stats.cpu_percent) if stats else None,
        "mem_used_bytes": stats.mem_used_bytes if stats else None,
    }


def containers_payload(server_id: str, docker: DockerSnapshot | None) -> dict:
    return {
        "server_id": server_id,
        "ts": docker.ts if docker else None,
        "containers": [container_row(c) for c in docker.containers] if docker else [],
    }
