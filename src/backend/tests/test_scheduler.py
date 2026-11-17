"""폴링 스케줄러 테스트.

서버가 끊겼을 때의 재시도 간격과, 실패의 영향 범위를 확인한다.
둘 다 조용히 망가지면 알아채기 어려운 부분이다.
"""

from __future__ import annotations

import asyncio

import pytest

from devdash.collect import scheduler as scheduler_module
from devdash.collect.scheduler import BACKOFF_MAX, ServerPoller
from devdash.config import ServerConfig
from devdash.store.memory import MemoryStore


def make_poller() -> ServerPoller:
    cfg = ServerConfig(id="a", host="10.0.0.1", user="u")
    return ServerPoller(cfg, MemoryStore())


@pytest.fixture
def sleeps(monkeypatch):
    """실제로 기다리지 않고, 얼마나 쉬려 했는지만 기록한다."""
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(scheduler_module.asyncio, "sleep", fake_sleep)
    return recorded


class _Steps:
    """정해진 순서대로 성공하거나 실패하고, 다 쓰면 루프를 멈춘다."""

    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes = list(outcomes)

    async def __call__(self) -> None:
        if not self._outcomes:
            raise asyncio.CancelledError
        if not self._outcomes.pop(0):
            raise RuntimeError("끊김")


async def test_실패하면_재시도_간격을_두_배씩_늘린다(sleeps):
    poller = make_poller()
    with pytest.raises(asyncio.CancelledError):
        await poller._repeat(5.0, _Steps([False, False, False]))
    assert sleeps == [1.0, 2.0, 4.0]


async def test_성공하면_간격이_다시_처음으로_돌아온다(sleeps):
    poller = make_poller()
    with pytest.raises(asyncio.CancelledError):
        await poller._repeat(5.0, _Steps([False, False, True, False]))
    # 실패 1초, 실패 2초, 성공하면 정상 주기 5초, 다시 실패하면 1초부터
    assert sleeps == [1.0, 2.0, 5.0, 1.0]


async def test_재시도_간격에는_상한이_있다(sleeps):
    poller = make_poller()
    with pytest.raises(asyncio.CancelledError):
        await poller._repeat(5.0, _Steps([False] * 10))
    assert max(sleeps) == BACKOFF_MAX
    assert sleeps[-1] == BACKOFF_MAX


class _FailingCollector:
    async def collect(self, conn) -> None:
        raise RuntimeError("끊김")


async def test_자원_수집이_실패하면_연결_상태가_error_가_된다():
    poller = make_poller()
    poller._system = _FailingCollector()
    with pytest.raises(RuntimeError):
        await poller._system_step()
    assert poller.state.status == "error"
    assert "끊김" in poller.state.error


async def test_docker_실패는_연결_상태를_건드리지_않는다():
    """권한이 없어 docker 만 실패해도 자원 수집은 정상으로 보여야 한다."""
    poller = make_poller()
    poller._docker = _FailingCollector()
    with pytest.raises(RuntimeError):
        await poller._docker_step()
    assert poller.state.status == "connecting"
    assert poller.state.error is None
    assert "끊김" in poller.state.docker_error


def test_메시지가_빈_예외도_알아볼_수_있게_적는다():
    """TimeoutError 처럼 본문이 없는 예외를 그대로 쓰면 "TimeoutError: " 가 된다."""
    assert scheduler_module._describe(TimeoutError()) == "TimeoutError"
    assert scheduler_module._describe(RuntimeError("끊김")) == "RuntimeError: 끊김"
