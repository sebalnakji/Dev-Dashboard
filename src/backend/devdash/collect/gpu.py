"""GPU 지표 수집.

nvidia-smi 로 읽는다. 실측 21ms 로 /proc 읽기만큼 싸기 때문에 별도 주기를 두지 않고
자원 지표와 같은 명령에 실어 보낸다. 그래야 SSH 왕복도 늘지 않고, 시계열도
자원 지표와 같은 시점으로 맞춰진다.

GPU 가 없는 서버에서도 명령 전체가 실패하지 않아야 한다. nvidia-smi 가 없으면
빈 출력을 내도록 해서, 그런 서버는 그냥 GPU 가 없는 것으로 다뤄진다.
"""

from __future__ import annotations

import logging

from devdash.models import GpuInfo

logger = logging.getLogger(__name__)

_FIELDS = "index,name,utilization.gpu,memory.used,memory.total"

GPU_COMMAND = (
    "command -v nvidia-smi >/dev/null 2>&1 && "
    f"nvidia-smi --query-gpu={_FIELDS} --format=csv,noheader,nounits 2>/dev/null "
    "|| true"
)

_MIB = 1024 * 1024


def parse_gpu(text: str) -> tuple[GpuInfo, ...]:
    """`nvidia-smi --format=csv,noheader,nounits` 출력을 파싱한다.

    한 줄이 "0, NVIDIA GeForce RTX 4070, 0, 3985, 12282" 형태다.

    사용률이 [N/A] 로 나오는 환경이 있는데, 그 줄을 통째로 버리면 VRAM 정보까지
    같이 사라진다. 사용률만 모르는 것으로 두고 나머지는 살린다.
    메모리 값까지 읽을 수 없는 줄만 건너뛴다. GPU 파싱이 실패해서
    CPU·메모리 수집까지 날아가면 안 되기 때문이다.
    """
    gpus: list[GpuInfo] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            utilization = float(parts[2])
        except ValueError:
            utilization = None
        try:
            gpus.append(
                GpuInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    utilization_percent=utilization,
                    memory_used_bytes=int(parts[3]) * _MIB,
                    memory_total_bytes=int(parts[4]) * _MIB,
                )
            )
        except ValueError:
            logger.warning("nvidia-smi 출력 한 줄을 해석하지 못했습니다: %r", line[:200])
    return tuple(gpus)
