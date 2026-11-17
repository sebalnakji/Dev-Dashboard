"""메모리 저장소 테스트."""

from __future__ import annotations

from devdash.models import CpuTimes, DockerSnapshot, NetCounters, SystemSample
from devdash.store.memory import MemoryStore


def make_sample(ts: float) -> SystemSample:
    return SystemSample(
        ts=ts,
        cpu_percent=None,
        cpu_model="테스트 CPU",
        cpu_cores=8,
        load1=0.0,
        load5=0.0,
        load15=0.0,
        uptime_seconds=0.0,
        mem_total_bytes=0,
        mem_available_bytes=0,
        swap_total_bytes=0,
        swap_free_bytes=0,
        disks=(),
        gpus=(),
        net_rx_bps=None,
        net_tx_bps=None,
        cpu_times=CpuTimes(total=0, idle=0),
        net_counters=NetCounters(rx_bytes=0, tx_bytes=0),
    )


def test_넣은_순서대로_돌려준다():
    store = MemoryStore()
    for ts in (1.0, 2.0, 3.0):
        store.add_sample("a", make_sample(ts))
    assert [s.ts for s in store.samples("a")] == [1.0, 2.0, 3.0]
    assert store.latest_sample("a").ts == 3.0


def test_최대_개수를_넘으면_오래된_것부터_밀린다():
    """링버퍼라 메모리가 무한히 늘지 않는다."""
    store = MemoryStore(max_samples=3)
    for ts in (1.0, 2.0, 3.0, 4.0, 5.0):
        store.add_sample("a", make_sample(ts))
    assert [s.ts for s in store.samples("a")] == [3.0, 4.0, 5.0]


def test_서버별로_따로_담긴다():
    store = MemoryStore()
    store.add_sample("a", make_sample(1.0))
    store.add_sample("b", make_sample(2.0))
    assert [s.ts for s in store.samples("a")] == [1.0]
    assert [s.ts for s in store.samples("b")] == [2.0]


def test_아직_수집하지_않은_서버는_빈_값():
    store = MemoryStore()
    assert store.samples("없음") == ()
    assert store.latest_sample("없음") is None
    assert store.docker("없음") is None


def test_컨테이너_상태는_최신_것으로_덮어쓴다():
    """컨테이너는 이력을 남기지 않고 현재 상태만 본다."""
    store = MemoryStore()
    store.set_docker("a", DockerSnapshot(ts=1.0, containers=()))
    store.set_docker("a", DockerSnapshot(ts=2.0, containers=()))
    assert store.docker("a").ts == 2.0
