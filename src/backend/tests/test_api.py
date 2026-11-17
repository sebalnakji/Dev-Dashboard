"""API 계층 테스트.

프론트엔드가 의존하는 계약이라 조용히 바뀌면 화면이 안 도는데,
브라우저를 열기 전까지는 그걸 모른다.

수집 스케줄러는 붙이지 않는다. 라우트는 저장소와 상태만 읽으면 되고,
테스트가 실제 SSH 접속을 시도해서는 안 되기 때문이다.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devdash.api.routes import _event, router, stream_events
from devdash.collect.scheduler import ServerState
from devdash.config import AppConfig, ServerConfig
from devdash.models import Container, ContainerStats, DockerSnapshot
from devdash.store.memory import MemoryStore
from tests.test_payloads import make_disk, make_sample


class FakeConnection:
    """실제로 접속하지 않고 명령만 받아 적는다."""

    def __init__(self, reply: str = "", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.commands: list[str] = []

    async def run(self, command: str) -> str:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return self.reply


class FakeScheduler:
    """라우트가 쓰는 건 states() 와 connection() 뿐이다."""

    def __init__(self, states: dict[str, ServerState], connection=None) -> None:
        self._states = states
        self._connection = connection

    def states(self) -> dict[str, ServerState]:
        return self._states

    def connection(self, server_id: str):
        return self._connection


@pytest.fixture
def config():
    return AppConfig(
        servers=[
            ServerConfig(id="a", host="10.0.0.1", user="u", label="개발 서버"),
            ServerConfig(id="b", host="10.0.0.2", user="u"),
        ]
    )


@pytest.fixture
def store():
    store = MemoryStore()
    for i in range(3):
        store.add_sample("a", make_sample(float(i), cpu=float(i), disks=(make_disk("/", 87, 13),)))
    store.set_docker(
        "a",
        DockerSnapshot(
            ts=9.0,
            containers=(
                Container(
                    id="abc",
                    name="web",
                    image="web:1",
                    state="running",
                    status="Up 3 hours",
                    ports="0.0.0.0:80->80/tcp",
                    stats=ContainerStats(cpu_percent=1.5, mem_used_bytes=100),
                ),
            ),
        ),
    )
    return store


@pytest.fixture
def connection():
    return FakeConnection(reply="첫 줄\n둘째 줄\n")


@pytest.fixture
def scheduler(connection):
    return FakeScheduler({"a": ServerState(status="online"), "b": ServerState()}, connection)


@pytest.fixture
def client(config, store, scheduler):
    app = FastAPI()
    app.state.config = config
    app.state.store = store
    app.state.scheduler = scheduler
    app.include_router(router)
    return TestClient(app)


def test_그리드_목록은_설정_순서대로_모든_서버를_준다(client):
    rows = client.get("/api/servers").json()
    assert [r["id"] for r in rows] == ["a", "b"]
    assert rows[0]["label"] == "개발 서버"
    assert rows[0]["status"] == "online"
    # 아직 수집되지 않은 서버도 카드가 그려져야 한다
    assert rows[1]["status"] == "connecting"
    assert rows[1]["cpu_percent"] is None


def test_그리드_목록에는_스파크라인이_붙는다(client):
    rows = client.get("/api/servers").json()
    assert rows[0]["cpu_spark"] == [0.0, 1.0, 2.0]
    assert rows[1]["cpu_spark"] == []


def test_시계열_조회(client):
    body = client.get("/api/servers/a/samples?points=2").json()
    assert body["server_id"] == "a"
    assert len(body["samples"]) == 2
    # 마지막 점은 항상 최신 샘플이어야 한다
    assert body["samples"][-1]["ts"] == 2.0


def test_점_개수가_범위를_벗어나면_거절한다(client):
    assert client.get("/api/servers/a/samples?points=0").status_code == 422


def test_컨테이너_조회(client):
    body = client.get("/api/servers/a/containers").json()
    assert body["ts"] == 9.0
    assert body["containers"][0]["name"] == "web"
    assert body["containers"][0]["cpu_percent"] == 1.5


def test_수집_전인_서버도_빈_응답을_준다(client):
    body = client.get("/api/servers/b/containers").json()
    assert body == {"server_id": "b", "ts": None, "containers": []}


def test_없는_서버는_404_와_안내를_준다(client):
    response = client.get("/api/servers/nope/containers")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "nope" in detail
    # str(KeyError) 가 따옴표를 덧씌우던 문제
    assert not detail.startswith('"')


def test_SSE_프레이밍():
    """event 와 data 를 빈 줄로 끊어야 브라우저가 한 건으로 인식한다."""
    text = _event("update", [{"id": "a"}])
    assert text == 'event: update\ndata: [{"id": "a"}]\n\n'


def test_SSE_는_한글을_그대로_보낸다():
    text = _event("update", {"label": "개발 서버"})
    assert "개발 서버" in text


async def test_SSE_는_바뀐_것만_보낸다(config, store, scheduler):
    """5초마다 같은 값을 다시 보내면 브라우저가 헛일을 한다."""
    events = []
    stream = stream_events(config, scheduler, store)
    events.append(await anext(stream))  # 첫 요약
    events.append(await anext(stream))  # 컨테이너 (a 만 수집돼 있다)

    assert events[0].startswith("event: update")
    assert events[1].startswith("event: containers")
    assert json.loads(events[1].split("data: ", 1)[1])["server_id"] == "a"

    # 값이 그대로면 다음 틱에는 아무것도 나가지 않는다.
    # 새 샘플을 넣어야 다시 요약이 나간다.
    store.add_sample("a", make_sample(99.0, cpu=50.0))
    assert (await anext(stream)).startswith("event: update")
    await stream.aclose()


def test_컨테이너_로그_조회(client, connection):
    body = client.get("/api/servers/a/containers/abc/logs?tail=50").json()
    assert body["name"] == "web"
    assert body["tail"] == 50
    assert body["text"] == "첫 줄\n둘째 줄\n"
    # 표준 오류도 로그의 일부라 합쳐서 가져온다
    assert connection.commands == ["docker logs --tail 50 abc 2>&1"]


def test_수집된_목록에_없는_id_는_거절한다(client, connection):
    """이 값이 그대로 원격 셸에 들어간다. 아무 문자열이나 통과시키면 명령을 끼워 넣을 수 있다."""
    response = client.get("/api/servers/a/containers/abc;%20rm%20-rf%20~/logs")
    assert response.status_code == 404
    assert connection.commands == []


def test_로그_명령이_실패하면_502(client, config, store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    broken = FakeConnection(error=RuntimeError("연결 끊김"))
    app = FastAPI()
    app.state.config = config
    app.state.store = store
    app.state.scheduler = FakeScheduler({"a": ServerState(), "b": ServerState()}, broken)
    app.include_router(router)

    response = TestClient(app).get("/api/servers/a/containers/abc/logs")
    assert response.status_code == 502
    assert "연결 끊김" in response.json()["detail"]
