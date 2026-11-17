"""servers.yaml 로드와 검증.

접속 정보만 담고 비밀 값은 담지 않는다. 비밀번호나 키 passphrase 가 필요하면
secret_from_keyring 을 켜서 OS 자격증명 저장소에서 읽는다.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class PollConfig(BaseModel):
    """수집 주기(초).

    docker stats 는 컨테이너가 많으면 1~2초 이상 걸린다. 시스템 지표 수집을
    막지 않도록 주기를 따로 둔다.
    """

    system: float = Field(default=5.0, gt=0)
    docker: float = Field(default=15.0, gt=0)


class ServerConfig(BaseModel):
    id: str
    host: str
    user: str
    label: str | None = None
    port: int = Field(default=22, gt=0, lt=65536)

    key_file: Path | None = None
    secret_from_keyring: bool = False
    """켜면 keyring 의 ("devdash", id) 에서 비밀 값을 읽는다.
    key_file 이 있으면 passphrase 로, 없으면 비밀번호로 쓴다."""

    verify_host_key: bool = True
    known_hosts: Path | None = None

    docker_sudo: bool = False
    control: bool = False
    """컨테이너 제어(start/stop/restart) 허용 여부. 기본은 읽기 전용이다."""

    poll: PollConfig = Field(default_factory=PollConfig)
    connect_timeout: float = Field(default=10.0, gt=0)
    command_timeout: float = Field(default=20.0, gt=0)

    @field_validator("key_file", "known_hosts")
    @classmethod
    def _expand_user(cls, v: Path | None) -> Path | None:
        # YAML 에 ~/.ssh/id_ed25519 처럼 쓰는 경우가 많다
        return v.expanduser() if v is not None else None

    @property
    def display_name(self) -> str:
        return self.label or self.id


class AppConfig(BaseModel):
    servers: list[ServerConfig]

    @field_validator("servers")
    @classmethod
    def _unique_ids(cls, servers: list[ServerConfig]) -> list[ServerConfig]:
        seen: set[str] = set()
        for s in servers:
            if s.id in seen:
                raise ValueError(f"서버 id 가 중복됩니다: {s.id}")
            seen.add(s.id)
        return servers

    def get(self, server_id: str) -> ServerConfig:
        for s in self.servers:
            if s.id == server_id:
                return s
        known = ", ".join(s.id for s in self.servers) or "(없음)"
        raise KeyError(f"'{server_id}' 서버를 찾을 수 없습니다. 설정된 서버: {known}")


DEFAULT_CONFIG_PATH = Path("config/servers.yaml")

ROOT = Path(__file__).resolve().parents[3]
"""저장소 루트. Claude 훅처럼 아무 폴더에서나 불리는 코드가 설정·로그를 찾을 때 쓴다."""


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"설정 파일이 없습니다: {path}\nconfig/servers.example.yaml 을 복사해서 만드세요."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)
