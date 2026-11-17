"""API 페이로드 테스트.

화면 계약이라 조용히 바뀌면 프론트엔드가 깨진다. 모양과 간추리기 규칙을 고정한다.
"""

from __future__ import annotations

from devdash.api.payloads import (
    busiest_disk,
    container_row,
    downsample,
    sample_point,
    samples_payload,
    server_summary,
)
from devdash.collect.scheduler import ServerState
from devdash.config import ServerConfig
from devdash.models import (
    Container,
    ContainerStats,
    CpuTimes,
    DiskUsage,
    DockerSnapshot,
    GpuInfo,
    NetCounters,
    SystemSample,
)


def make_disk(mount: str, used: int, avail: int) -> DiskUsage:
    return DiskUsage(
        mount=mount,
        fstype="ext4",
        total_bytes=used + avail,
        used_bytes=used,
        available_bytes=avail,
    )


def make_sample(ts: float, cpu: float | None = 1.0, disks=()) -> SystemSample:
    return SystemSample(
        ts=ts,
        cpu_percent=cpu,
        cpu_model="테스트 CPU",
        cpu_cores=8,
        load1=0.1,
        load5=0.2,
        load15=0.3,
        uptime_seconds=100.0,
        mem_total_bytes=1000,
        mem_available_bytes=750,
        swap_total_bytes=0,
        swap_free_bytes=0,
        disks=disks,
        gpus=(
            GpuInfo(
                index=0,
                name="RTX 4070",
                utilization_percent=12.0,
                memory_used_bytes=300,
                memory_total_bytes=1000,
            ),
        ),
        net_rx_bps=1024.4,
        net_tx_bps=None,
        cpu_times=CpuTimes(total=0, idle=0),
        net_counters=NetCounters(rx_bytes=0, tx_bytes=0),
    )


def test_카드는_가장_많이_찬_파티션을_고른다():
    sample = make_sample(1.0, disks=(make_disk("/", 10, 90), make_disk("/data", 80, 20)))
    assert busiest_disk(sample) == {
        "mount": "/data",
        "percent": 80.0,
        "used_bytes": 80,
        # 퍼센트와 맞아떨어지도록 예약 블록을 뺀 값을 전체로 본다
        "total_bytes": 100,
    }


def test_디스크가_없으면_None():
    assert busiest_disk(make_sample(1.0)) is None


def test_카드_요약은_카드에_그리는_값만_담는다():
    cfg = ServerConfig(id="a", host="h", user="u", label="개발 서버")
    summary = server_summary(
        cfg,
        ServerState(status="online"),
        make_sample(1.0, cpu=4.2, disks=(make_disk("/", 87, 13),)),
        DockerSnapshot(ts=1.0, containers=()),
    )
    assert summary["label"] == "개발 서버"
    assert summary["cpu_percent"] == 4.2
    assert summary["mem_percent"] == 25.0
    # 퍼센트만으로는 여유분을 알 수 없어 실제 용량도 함께 준다
    assert summary["mem_used_bytes"] == 250
    assert summary["mem_total_bytes"] == 1000
    assert summary["cpu_model"] == "테스트 CPU"
    assert summary["cpu_cores"] == 8
    assert summary["gpus"] == [
        {
            "index": 0,
            "utilization_percent": 12.0,
            "memory_percent": 30.0,
            "name": "RTX 4070",
            "memory_used_bytes": 300,
            "memory_total_bytes": 1000,
        }
    ]
    assert summary["containers"] == {"running": 0, "stopped": 0}
    # 카드에 없는 값은 나가지 않는다
    assert "load1" not in summary
    assert "net_rx_bps" not in summary
    # 스파크라인은 초기 로드에서만 붙는다
    assert "cpu_spark" not in summary


def test_수집_전에는_값이_비어_있어도_카드가_그려진다():
    cfg = ServerConfig(id="a", host="h", user="u")
    summary = server_summary(cfg, ServerState(), None, None)
    assert summary["status"] == "connecting"
    assert summary["cpu_percent"] is None
    assert summary["containers"] is None
    assert summary["gpus"] == []


def test_상세_차트_점은_메모리를_퍼센트와_절대값_둘_다_준다():
    point = sample_point(make_sample(1.0))
    assert point["mem_percent"] == 25.0
    assert point["mem_used_bytes"] == 250
    assert point["net_tx_bps"] is None


def test_간추릴_때_스파이크를_살린다():
    """일정 간격으로 건너뛰면 한 샘플짜리 스파이크가 사라진다."""
    samples = tuple(make_sample(float(i), cpu=1.0) for i in range(100))
    samples = samples[:37] + (make_sample(37.0, cpu=99.0),) + samples[38:]

    picked = downsample(samples, 10)
    assert len(picked) == 10
    assert max(s.cpu_percent for s in picked) == 99.0


def test_간추린_결과의_마지막은_항상_최신_샘플():
    """그래프 오른쪽 끝은 현재 상태여야 한다."""
    samples = tuple(make_sample(float(i)) for i in range(100))
    picked = downsample(samples, 10)
    assert picked[-1].ts == 99.0


def test_샘플이_적으면_그대로_돌려준다():
    samples = tuple(make_sample(float(i)) for i in range(5))
    assert downsample(samples, 600) == list(samples)


def test_전체_용량은_봉투에_한_번만_담는다():
    """점마다 같은 값을 반복해 보내지 않는다."""
    payload = samples_payload("a", (make_sample(1.0), make_sample(2.0)), 600)
    assert payload["mem_total_bytes"] == 1000
    assert "mem_total_bytes" not in payload["samples"][0]


def test_빈_저장소도_응답이_된다():
    payload = samples_payload("a", (), 600)
    assert payload == {"server_id": "a", "mem_total_bytes": None, "samples": []}


def test_컨테이너_행():
    container = Container(
        id="abc",
        name="web",
        image="web:1",
        state="running",
        status="Up 3 hours",
        ports="0.0.0.0:80->80/tcp",
        stats=ContainerStats(cpu_percent=1.234, mem_used_bytes=100),
    )
    row = container_row(container)
    assert row["cpu_percent"] == 1.2
    assert row["mem_used_bytes"] == 100


def test_멈춘_컨테이너는_사용량이_비어_있다():
    container = Container(
        id="abc", name="web", image="web:1", state="exited", status="Exited", ports=""
    )
    row = container_row(container)
    assert row["cpu_percent"] is None
    assert row["mem_used_bytes"] is None
