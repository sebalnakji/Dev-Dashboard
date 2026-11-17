"""시스템 지표 파서 테스트.

실제 서버의 /proc 출력을 tests/fixtures 에 그대로 떠 두고 파싱을 검증한다.
원격 서버 없이 회귀 테스트가 가능해야, 파서를 고칠 때 안심하고 손댈 수 있다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devdash.collect.system import (
    parse_cpu_times,
    parse_df,
    parse_net_dev,
    parse_system_output,
    split_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def sample1():
    return parse_system_output(read_fixture("system-1.txt"), now=1000.0, prev=None)


@pytest.fixture
def sample2(sample1):
    # 2초 뒤에 두 번째 수집이 일어난 상황
    return parse_system_output(read_fixture("system-2.txt"), now=1002.0, prev=sample1)


def test_섹션_분리():
    sections = split_sections(read_fixture("system-1.txt"))
    assert set(sections) == {
        "/proc/stat",
        "/proc/meminfo",
        "/proc/loadavg",
        "/proc/uptime",
        "/proc/net/dev",
        "df",
        "cpu",
        "gpu",
    }


def test_cpu_누적값_파싱():
    cpu = parse_cpu_times(read_fixture("system-1.txt").split("@@/proc/meminfo")[0])
    # user+nice+system+idle+iowait+irq+softirq+steal
    assert cpu.total == 10_340_000
    # idle + iowait
    assert cpu.idle == 9_020_000


def test_첫_샘플은_cpu_사용률을_알_수_없다(sample1):
    """/proc/stat 은 부팅 이후 누적값이라 한 번 읽어서는 사용률을 계산할 수 없다."""
    assert sample1.cpu_percent is None
    assert sample1.net_rx_bps is None
    assert sample1.net_tx_bps is None


def test_두_샘플의_차이로_cpu_사용률을_구한다(sample2):
    # total 차이 400, idle 차이 300 -> busy 100/400
    assert sample2.cpu_percent == pytest.approx(25.0)


def test_네트워크_속도는_경과_시간으로_나눈다(sample2):
    # eth0 수신 2,000,000 바이트 / 2초
    assert sample2.net_rx_bps == pytest.approx(1_000_000.0)
    assert sample2.net_tx_bps == pytest.approx(300_000.0)


def test_lo_와_docker_인터페이스는_집계에서_뺀다():
    net = parse_net_dev(read_fixture("system-1.txt").split("@@/proc/net/dev")[1].split("@@df")[0])
    # eth0 만 계산에 들어간다
    assert net.rx_bytes == 1_000_000_000
    assert net.tx_bytes == 500_000_000


def test_메모리는_MemAvailable_기준으로_계산한다(sample1):
    assert sample1.mem_total_bytes == 16_341_236 * 1024
    assert sample1.mem_available_bytes == 8_123_456 * 1024
    assert sample1.mem_used_bytes == (16_341_236 - 8_123_456) * 1024
    assert sample1.mem_percent == pytest.approx(50.29, abs=0.01)


def test_로드와_가동시간(sample1):
    assert (sample1.load1, sample1.load5, sample1.load15) == (1.20, 0.98, 0.75)
    assert sample1.uptime_seconds == pytest.approx(273600.45)


def test_디스크_사용률은_df_와_같은_기준으로_계산한다(sample1):
    """df 의 Capacity 는 used/(used+available) 이다.

    예약 블록 때문에 total 로 나누면 df 출력과 값이 어긋난다.
    """
    by_mount = {d.mount: d for d in sample1.disks}
    assert set(by_mount) >= {"/", "/data"}

    root = by_mount["/"]
    assert root.fstype == "ext4"
    assert root.percent == pytest.approx(25.92, abs=0.01)  # df 출력은 26%

    data = by_mount["/data"]
    assert data.percent == pytest.approx(81.25, abs=0.01)  # df 출력은 82%


def test_마운트_경로에_공백이_있어도_파싱된다():
    text = (
        "Filesystem     Type 1024-blocks     Used Available Capacity Mounted on\n"
        "/dev/sdc1      ext4      1024000   512000    512000      50% /mnt/my data\n"
    )
    disks = parse_df(text)
    assert len(disks) == 1
    assert disks[0].mount == "/mnt/my data"


def test_카운터가_초기화되면_사용률을_내지_않는다(sample2):
    """재부팅하면 누적값이 0부터 다시 시작해 차이가 음수가 된다."""
    restarted = parse_system_output(read_fixture("system-1.txt"), now=1004.0, prev=sample2)
    assert restarted.cpu_percent is None
    assert restarted.net_rx_bps is None


def test_섹션이_빠지면_오류를_낸다():
    with pytest.raises(ValueError, match="빠진 섹션"):
        parse_system_output("@@/proc/stat\ncpu  1 2 3 4 5 6 7 8\n")


def test_가상_파일시스템은_디스크_목록에서_뺀다(sample1):
    """efivarfs, tmpfs 같은 것은 저장 장치가 아니라 목록만 어지럽힌다."""
    mounts = {d.mount for d in sample1.disks}
    assert "/sys/firmware/efi/efivars" not in mounts
    assert "/dev/shm" not in mounts
    # 실제 파티션은 남는다
    assert mounts == {"/", "/data", "/boot/efi"}


def test_GPU_파싱(sample1):
    """nvidia-smi 출력이 /proc 배치와 같은 명령으로 들어온다."""
    assert len(sample1.gpus) == 2
    gpu = sample1.gpus[0]
    assert gpu.index == 0
    assert gpu.name == "NVIDIA GeForce RTX 4070"
    assert gpu.utilization_percent == 12.0
    assert gpu.memory_used_bytes == 3985 * 1024 * 1024
    assert gpu.memory_percent == pytest.approx(32.45, abs=0.01)


def test_사용률이_NA_여도_VRAM_은_살린다(sample1):
    """줄을 통째로 버리면 VRAM 정보까지 같이 사라진다."""
    gpu = sample1.gpus[1]
    assert gpu.utilization_percent is None
    assert gpu.memory_used_bytes == 100 * 1024 * 1024


def test_GPU_가_없는_서버는_빈_목록():
    """nvidia-smi 가 없으면 그 자리가 비어서 온다. 자원 수집은 그대로 되어야 한다."""
    from devdash.collect.system import parse_system_output

    raw = read_fixture("system-1.txt").split("@@gpu")[0] + "@@gpu\n"
    sample = parse_system_output(raw, now=1000.0)
    assert sample.gpus == ()
    assert sample.mem_total_bytes > 0


def test_CPU_모델과_코어_수(sample1):
    assert sample1.cpu_cores == 20
    assert sample1.cpu_model == "Intel(R) Core(TM) i5-14500"


def test_모델명이_없는_환경도_견딘다():
    """ARM 처럼 /proc/cpuinfo 에 model name 이 없는 서버가 있다."""
    from devdash.collect.system import parse_cpuinfo

    assert parse_cpuinfo("4\n") == ("", 4)
    assert parse_cpuinfo("") == ("", 0)
