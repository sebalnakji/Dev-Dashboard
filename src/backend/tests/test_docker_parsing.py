"""Docker 수집 파서 테스트.

픽스처는 실제 서버 출력의 형식을 그대로 따르되, 내부 서비스 이름은 담지 않는다.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from devdash.collect.docker import (
    list_command,
    merge_stats,
    parse_container_list,
    parse_percent,
    parse_size,
    parse_size_pair,
    parse_stats,
    stats_command,
)
from devdash.models import DockerSnapshot

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def containers():
    return parse_container_list(read_fixture("docker-ps.txt"))


@pytest.fixture
def stats():
    return parse_stats(read_fixture("docker-stats.txt"))


def test_컨테이너_목록_파싱(containers):
    assert [c.name for c in containers] == ["web", "api", "batch", "cache"]
    web = containers[0]
    assert web.id == "aaaa11112222"
    assert web.image == "web:1.0.0"
    assert web.state == "running"
    assert web.status == "Up 42 minutes"
    assert "8080->8080/tcp" in web.ports


def test_실행_여부는_state_로_판단한다(containers):
    running = {c.name for c in containers if c.running}
    assert running == {"web", "api"}
    # paused 는 "Up ... (Paused)" 라 status 문구만 보면 실행 중처럼 보인다
    cache = next(c for c in containers if c.name == "cache")
    assert cache.status.startswith("Up")
    assert not cache.running


def test_스냅샷은_실행과_중지_개수를_센다(containers):
    snapshot = DockerSnapshot(ts=time.time(), containers=containers)
    assert snapshot.running_count == 2
    assert snapshot.stopped_count == 2


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0B", 0),
        ("39.24MiB", int(39.24 * 1024**2)),  # 메모리는 이진 단위
        ("3.95MB", int(3.95 * 1000**2)),  # 네트워크는 십진 단위
        ("16.4kB", 16400),
        ("1.5GiB", int(1.5 * 1024**3)),
        ("--", None),
        ("", None),
    ],
)
def test_크기_단위_변환(text, expected):
    """Docker 는 메모리에 이진 단위(MiB), 네트워크에 십진 단위(MB)를 섞어 쓴다."""
    assert parse_size(text) == expected


def test_슬래시로_묶인_두_값을_나눈다():
    used, limit = parse_size_pair("39.24MiB / 62.44GiB")
    assert used == int(39.24 * 1024**2)
    assert limit == int(62.44 * 1024**3)
    assert parse_size_pair("-- / --") == (None, None)
    assert parse_size_pair("없음") == (None, None)


def test_퍼센트_파싱():
    assert parse_percent("12.34%") == pytest.approx(12.34)
    assert parse_percent("0.00%") == 0.0
    assert parse_percent("--") is None
    assert parse_percent(None) is None


def test_사용량_파싱(stats):
    web = stats["aaaa11112222"]
    assert web.cpu_percent == 0.0
    assert web.mem_used_bytes == int(39.24 * 1024**2)

    api = stats["bbbb11112222"]
    assert api.cpu_percent == pytest.approx(12.34)
    assert api.mem_used_bytes == int(1.5 * 1024**3)


def test_목록에_사용량을_붙인다(containers, stats):
    merged = merge_stats(containers, stats)
    by_name = {c.name: c for c in merged}

    assert by_name["web"].stats is not None
    assert by_name["api"].stats.cpu_percent == pytest.approx(12.34)
    # 멈춘 컨테이너는 stats 가 없다
    assert by_name["batch"].stats is None
    assert by_name["cache"].stats is None
    # stats 에만 있고 목록에 없는 컨테이너는 버린다
    assert len(merged) == len(containers)


def test_깨진_줄이_있어도_나머지는_살린다():
    output = (
        '{"id":"aaaa","name":"ok","image":"i","state":"running",'
        '"status":"Up","ports":"","created":"x"}\n'
        "이건 JSON 이 아니다\n"
        '{"id":"bbbb","name":"ok2","image":"i","state":"exited",'
        '"status":"Exited","ports":"","created":"x"}\n'
    )
    assert [c.name for c in parse_container_list(output)] == ["ok", "ok2"]


def test_sudo_설정이_명령에_반영된다():
    assert list_command(sudo=False).startswith("docker ps -a")
    assert list_command(sudo=True).startswith("sudo -n docker ps -a")
    assert stats_command(sudo=True).startswith("sudo -n docker stats --no-stream")
    # 라벨을 통째로 가져오지 않도록 필드를 좁혔는지
    assert "{{json .ID}}" in list_command()
    assert "{{json .}}" not in list_command()
