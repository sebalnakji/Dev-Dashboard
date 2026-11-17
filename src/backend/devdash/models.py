"""수집 결과를 담는 공용 타입.

수집 계층(collect)과 저장 계층(store), API 계층이 모두 이 타입을 주고받는다.
파싱 결과를 여기서 정규화해 두면, 나중에 수집 방식을 바꿔도(예: docker CLI 파싱 →
Docker Engine API) 호출부를 고치지 않아도 된다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CpuTimes:
    """/proc/stat 첫 줄의 누적 jiffies.

    부팅 이후 누적값이라 이것만으로는 사용률을 알 수 없다.
    두 시점의 차이를 나눠야 사용률이 나온다.
    """

    total: int
    idle: int


@dataclass(frozen=True)
class NetCounters:
    """네트워크 누적 송수신 바이트. CPU와 마찬가지로 차이를 내야 속도가 된다."""

    rx_bytes: int
    tx_bytes: int


@dataclass(frozen=True)
class DiskUsage:
    """마운트 지점 하나의 사용량."""

    mount: str
    fstype: str
    total_bytes: int
    used_bytes: int
    available_bytes: int

    @property
    def percent(self) -> float:
        """사용률.

        total 로 나누지 않고 used/(used+available) 로 계산한다.
        리눅스 파일시스템은 일부 블록을 root 용으로 예약해 두기 때문에
        total 기준으로 계산하면 `df` 가 보여주는 값과 어긋난다.
        """
        denominator = self.used_bytes + self.available_bytes
        if denominator <= 0:
            return 0.0
        return self.used_bytes / denominator * 100.0


@dataclass(frozen=True)
class GpuInfo:
    """GPU 한 장의 상태. nvidia-smi 가 없는 서버에서는 아예 목록이 비어 있다."""

    index: int
    name: str
    utilization_percent: float | None
    """일부 환경에서는 nvidia-smi 가 [N/A] 를 내놓는다. 그때는 None 이다."""

    memory_used_bytes: int
    memory_total_bytes: int

    @property
    def memory_percent(self) -> float:
        if self.memory_total_bytes <= 0:
            return 0.0
        return self.memory_used_bytes / self.memory_total_bytes * 100.0


@dataclass(frozen=True)
class SystemSample:
    """한 시점의 시스템 지표.

    cpu_percent 와 net_*_bps 는 직전 샘플이 있어야 계산된다.
    첫 수집에서는 None 이며, 화면에서는 "측정 중"으로 표시한다.
    """

    ts: float
    """수집 시각 (epoch 초)."""

    cpu_percent: float | None

    cpu_model: str
    """모델명. 바뀌지 않는 값이지만 샘플마다 함께 온다."""

    cpu_cores: int

    load1: float
    load5: float
    load15: float
    uptime_seconds: float

    mem_total_bytes: int
    mem_available_bytes: int
    swap_total_bytes: int
    swap_free_bytes: int

    disks: tuple[DiskUsage, ...]
    gpus: tuple[GpuInfo, ...]

    net_rx_bps: float | None
    net_tx_bps: float | None

    cpu_times: CpuTimes
    """다음 샘플에서 차이를 내기 위해 보관하는 누적값."""

    net_counters: NetCounters
    """다음 샘플에서 차이를 내기 위해 보관하는 누적값."""

    @property
    def mem_used_bytes(self) -> int:
        return self.mem_total_bytes - self.mem_available_bytes

    @property
    def mem_percent(self) -> float:
        if self.mem_total_bytes <= 0:
            return 0.0
        return self.mem_used_bytes / self.mem_total_bytes * 100.0

    @property
    def swap_used_bytes(self) -> int:
        return self.swap_total_bytes - self.swap_free_bytes


@dataclass(frozen=True)
class ContainerStats:
    """`docker stats` 한 줄. 실행 중이 아닌 컨테이너는 이 값이 없다.

    같은 명령이 네트워크·블록 IO, PID 수, 메모리 사용률도 함께 내주지만
    화면에서 쓰지 않아 받지 않는다. 필요해지면 수집 포맷에 한 줄씩 되살리면 된다.
    """

    cpu_percent: float | None
    mem_used_bytes: int | None


@dataclass(frozen=True)
class Container:
    """컨테이너 하나의 상태.

    목록(docker ps)과 사용량(docker stats)은 수집 주기가 달라 따로 들어온다.
    stats 는 아직 안 받았거나 멈춘 컨테이너면 None 이다.
    """

    id: str
    name: str
    image: str
    state: str
    """running, exited, created, paused, restarting, removing, dead 중 하나."""

    status: str
    """"Up 41 minutes" 처럼 사람이 읽는 상태 문구. 생성 시각도 여기에 녹아 있다."""

    ports: str
    stats: ContainerStats | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"


@dataclass(frozen=True)
class DockerSnapshot:
    ts: float
    containers: tuple[Container, ...]

    @property
    def running_count(self) -> int:
        return sum(1 for c in self.containers if c.running)

    @property
    def stopped_count(self) -> int:
        return len(self.containers) - self.running_count
