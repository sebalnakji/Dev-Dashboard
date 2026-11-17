"""메모리 링버퍼 저장소.

고정 크기 덱에 담아 오래된 샘플부터 밀어낸다. 크기가 고정이라 메모리가
무한히 늘어날 위험이 없는 대신, 프로세스를 다시 시작하면 이력이 사라진다.
그 트레이드오프의 근거는 [[0003-지표를-메모리에-보관]] 에 있다.
"""

from __future__ import annotations

from collections import deque

from devdash.models import DockerSnapshot, SystemSample
from devdash.store.base import MetricStore

DEFAULT_MAX_SAMPLES = 2160
"""5초 주기 기준 약 3시간. 수집 주기를 늘리면 그만큼 더 긴 기간을 담는다."""


class MemoryStore(MetricStore):
    def __init__(self, max_samples: int = DEFAULT_MAX_SAMPLES) -> None:
        self._max_samples = max_samples
        self._samples: dict[str, deque[SystemSample]] = {}
        self._docker: dict[str, DockerSnapshot] = {}

    def _bucket(self, server_id: str) -> deque[SystemSample]:
        bucket = self._samples.get(server_id)
        if bucket is None:
            bucket = self._samples[server_id] = deque(maxlen=self._max_samples)
        return bucket

    def add_sample(self, server_id: str, sample: SystemSample) -> None:
        self._bucket(server_id).append(sample)

    def samples(self, server_id: str) -> tuple[SystemSample, ...]:
        return tuple(self._samples.get(server_id, ()))

    def latest_sample(self, server_id: str) -> SystemSample | None:
        bucket = self._samples.get(server_id)
        return bucket[-1] if bucket else None

    def set_docker(self, server_id: str, snapshot: DockerSnapshot) -> None:
        self._docker[server_id] = snapshot

    def docker(self, server_id: str) -> DockerSnapshot | None:
        return self._docker.get(server_id)
