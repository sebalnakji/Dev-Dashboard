"""시스템 자원 지표 수집.

/proc 파일을 직접 읽는다. top 이나 vmstat 같은 외부 도구에 의존하지 않으므로
배포판 차이에 강하다.

지표마다 명령을 따로 실행하면 SSH 왕복 지연이 누적된다. 그래서 필요한 파일을
한 번의 명령으로 모두 읽고 구분자로 쪼갠다.
"""

from __future__ import annotations

import time

from devdash.collect.gpu import GPU_COMMAND, parse_gpu
from devdash.models import CpuTimes, DiskUsage, NetCounters, SystemSample

SECTION_MARK = "@@"

BATCH_COMMAND = (
    "for f in /proc/stat /proc/meminfo /proc/loadavg /proc/uptime /proc/net/dev; do "
    'echo "@@$f"; cat "$f"; '
    "done; "
    'echo "@@df"; '
    # -l 로 로컬 파일시스템만 본다. 죽은 CIFS/NFS 마운트가 있으면 df 가 거기서 멈춰
    # 자원 수집 전체가 막힌다 (응답 없는 CIFS 마운트가 있는 서버에서 실제로 발생).
    # timeout 은 그래도 멈출 때의 안전장치 — df 가 비면 디스크만 빠지고 나머지는 정상 수집된다.
    "timeout 5 df -l -P -T; "
    # 코어 수와 모델명은 바뀌지 않지만, 따로 한 번만 받아오려면 연결마다 상태를
    # 들고 있어야 한다. 두 줄짜리라 그냥 같이 읽는 편이 단순하다.
    'echo "@@cpu"; '
    'nproc; grep -m1 "^model name" /proc/cpuinfo | cut -d: -f2- | sed "s/^ *//"; '
    # GPU 는 nvidia-smi 로 읽지만 21ms 로 싸서 같은 왕복에 실어 보낸다
    'echo "@@gpu"; '
    f"{GPU_COMMAND}"
)

# 실제 저장 장치가 아닌 가상 파일시스템. 용량을 봐야 할 대상이 아니라 목록만 어지럽힌다.
# df 의 -x 옵션 대신 여기서 거른다. busybox df 처럼 -x 를 지원하지 않는 환경도 있고,
# 무엇을 왜 뺐는지 코드에 남는 편이 낫다.
_PSEUDO_FSTYPES = frozenset(
    {
        "autofs",
        "binfmt_misc",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "none",
        "nsfs",
        "overlay",
        "proc",
        "pstore",
        "ramfs",
        "securityfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)

# 집계에서 빼는 네트워크 인터페이스.
# lo 는 자기 자신과의 통신이고, docker/veth/br- 은 컨테이너 트래픽이라
# 물리 인터페이스와 함께 더하면 같은 트래픽을 두 번 세게 된다.
_SKIP_IFACE_PREFIXES = ("lo", "veth", "docker", "br-")


def split_sections(output: str) -> dict[str, str]:
    """`@@이름` 줄을 기준으로 배치 출력을 섹션별로 쪼갠다."""
    sections: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    for line in output.splitlines():
        if line.startswith(SECTION_MARK):
            if name is not None:
                sections[name] = "\n".join(buf)
            name = line[len(SECTION_MARK) :].strip()
            buf = []
        elif name is not None:
            buf.append(line)
    if name is not None:
        sections[name] = "\n".join(buf)
    return sections


def parse_cpu_times(stat_text: str) -> CpuTimes:
    """/proc/stat 첫 줄에서 누적 jiffies 를 뽑는다.

    guest 계열 필드는 user/nice 에 이미 포함되어 있어 더하지 않는다.
    """
    for line in stat_text.splitlines():
        if not line.startswith("cpu "):
            continue
        fields = [int(v) for v in line.split()[1:9]]
        total = sum(fields)
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
        return CpuTimes(total=total, idle=idle)
    raise ValueError("/proc/stat 에서 'cpu ' 줄을 찾지 못했습니다")


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo 를 바이트 단위 딕셔너리로 바꾼다."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            amount = int(parts[0])
        except ValueError:
            continue
        # 단위가 없는 항목(개수 등)은 그대로, kB 는 바이트로 환산한다
        values[key] = amount * 1024 if len(parts) > 1 else amount
    return values


def parse_cpuinfo(text: str) -> tuple[str, int]:
    """첫 줄은 코어 수(`nproc`), 둘째 줄은 모델명.

    ARM 처럼 /proc/cpuinfo 에 "model name" 이 없는 환경도 있어, 없으면 빈 문자열이다.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cores = 0
    if lines:
        try:
            cores = int(lines[0])
        except ValueError:
            cores = 0
    model = lines[1] if len(lines) > 1 else ""
    return model, cores


def parse_loadavg(text: str) -> tuple[float, float, float]:
    parts = text.split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def parse_uptime(text: str) -> float:
    return float(text.split()[0])


def parse_net_dev(text: str) -> NetCounters:
    """/proc/net/dev 에서 물리 인터페이스의 누적 송수신 바이트를 합산한다."""
    rx = tx = 0
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if not sep:
            continue  # 머리말 두 줄
        iface = name.strip()
        if iface.startswith(_SKIP_IFACE_PREFIXES):
            continue
        fields = rest.split()
        if len(fields) < 9:
            continue
        rx += int(fields[0])
        tx += int(fields[8])
    return NetCounters(rx_bytes=rx, tx_bytes=tx)


def parse_df(text: str) -> tuple[DiskUsage, ...]:
    """`df -P -T` 출력을 파싱한다.

    마운트 경로에 공백이 있을 수 있어 앞의 6개 필드만 잘라내고 나머지를 경로로 본다.
    가상 파일시스템(_PSEUDO_FSTYPES)은 저장 용량을 볼 대상이 아니라 제외한다.
    """
    disks: list[DiskUsage] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("Filesystem"):
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        _filesystem, fstype, blocks, used, avail, _capacity, mount = parts
        if fstype in _PSEUDO_FSTYPES:
            continue
        try:
            total_bytes = int(blocks) * 1024
            used_bytes = int(used) * 1024
            available_bytes = int(avail) * 1024
        except ValueError:
            continue
        disks.append(
            DiskUsage(
                mount=mount.strip(),
                fstype=fstype,
                total_bytes=total_bytes,
                used_bytes=used_bytes,
                available_bytes=available_bytes,
            )
        )
    return tuple(disks)


def _cpu_percent(current: CpuTimes, prev: SystemSample | None) -> float | None:
    """누적 jiffies 두 시점의 차이로 사용률을 구한다.

    직전 샘플이 없으면(첫 수집) 계산할 수 없다. 재부팅으로 카운터가 초기화된
    경우에도 차이가 음수가 되므로 None 을 돌려준다.
    """
    if prev is None:
        return None
    total_delta = current.total - prev.cpu_times.total
    idle_delta = current.idle - prev.cpu_times.idle
    if total_delta <= 0:
        return None
    busy_ratio = (total_delta - idle_delta) / total_delta
    return max(0.0, min(100.0, busy_ratio * 100.0))


def _net_speed(
    current: NetCounters, prev: SystemSample | None, now: float
) -> tuple[float | None, float | None]:
    if prev is None:
        return None, None
    elapsed = now - prev.ts
    if elapsed <= 0:
        return None, None
    rx_delta = current.rx_bytes - prev.net_counters.rx_bytes
    tx_delta = current.tx_bytes - prev.net_counters.tx_bytes
    if rx_delta < 0 or tx_delta < 0:
        return None, None  # 카운터 초기화
    return rx_delta / elapsed, tx_delta / elapsed


def parse_system_output(
    raw: str, now: float | None = None, prev: SystemSample | None = None
) -> SystemSample:
    """배치 명령의 출력 전체를 하나의 샘플로 바꾼다.

    SSH 없이도 테스트할 수 있도록 순수 함수로 둔다.
    """
    now = time.time() if now is None else now
    sections = split_sections(raw)

    missing = {"/proc/stat", "/proc/meminfo", "/proc/loadavg", "/proc/uptime"} - set(sections)
    if missing:
        raise ValueError(f"수집 출력에 빠진 섹션이 있습니다: {sorted(missing)}")

    cpu_times = parse_cpu_times(sections["/proc/stat"])
    mem = parse_meminfo(sections["/proc/meminfo"])
    load1, load5, load15 = parse_loadavg(sections["/proc/loadavg"])
    uptime = parse_uptime(sections["/proc/uptime"])
    net = parse_net_dev(sections.get("/proc/net/dev", ""))
    disks = parse_df(sections.get("df", ""))
    gpus = parse_gpu(sections.get("gpu", ""))
    cpu_model, cpu_cores = parse_cpuinfo(sections.get("cpu", ""))

    mem_total = mem.get("MemTotal", 0)
    # MemAvailable 은 커널 3.14 부터 있다. 없으면 근사치로 대체한다.
    mem_available = mem.get(
        "MemAvailable",
        mem.get("MemFree", 0) + mem.get("Buffers", 0) + mem.get("Cached", 0),
    )

    rx_bps, tx_bps = _net_speed(net, prev, now)

    return SystemSample(
        ts=now,
        cpu_percent=_cpu_percent(cpu_times, prev),
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        load1=load1,
        load5=load5,
        load15=load15,
        uptime_seconds=uptime,
        mem_total_bytes=mem_total,
        mem_available_bytes=mem_available,
        swap_total_bytes=mem.get("SwapTotal", 0),
        swap_free_bytes=mem.get("SwapFree", 0),
        disks=disks,
        gpus=gpus,
        net_rx_bps=rx_bps,
        net_tx_bps=tx_bps,
        cpu_times=cpu_times,
        net_counters=net,
    )


class SystemCollector:
    """서버 하나의 시스템 지표를 반복 수집한다.

    CPU 사용률과 네트워크 속도가 누적값의 차이로 계산되므로,
    직전 샘플을 들고 있어야 한다. 서버마다 인스턴스를 하나씩 둔다.
    """

    def __init__(self) -> None:
        self._prev: SystemSample | None = None

    async def collect(self, conn) -> SystemSample:
        raw = await conn.run(BATCH_COMMAND)
        sample = parse_system_output(raw, now=time.time(), prev=self._prev)
        self._prev = sample
        return sample
