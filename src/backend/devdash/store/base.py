"""저장 계층의 인터페이스.

[[0003-지표를-메모리에-보관]] 에서 정한 교체 지점이다. 지금 구현은 메모리 하나뿐이지만,
보관 기간을 늘려야 하면 이 인터페이스를 구현한 SQLite 저장소로 갈아끼운다.
API 계층은 이 인터페이스만 알고 있어야 한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from devdash.models import DockerSnapshot, SystemSample


class MetricStore(ABC):
    @abstractmethod
    def add_sample(self, server_id: str, sample: SystemSample) -> None:
        """새 자원 지표 샘플을 넣는다."""

    @abstractmethod
    def samples(self, server_id: str) -> tuple[SystemSample, ...]:
        """보관 중인 샘플을 오래된 것부터 돌려준다."""

    @abstractmethod
    def latest_sample(self, server_id: str) -> SystemSample | None:
        """가장 최근 샘플. 아직 하나도 없으면 None."""

    @abstractmethod
    def set_docker(self, server_id: str, snapshot: DockerSnapshot) -> None:
        """컨테이너 상태를 최신 것으로 덮어쓴다. 이력은 남기지 않는다."""

    @abstractmethod
    def docker(self, server_id: str) -> DockerSnapshot | None:
        """가장 최근 컨테이너 상태. 아직 없으면 None."""
