"""REST 엔드포인트와 SSE 스트림.

초기 로드는 REST 로 한 번 받고, 이후 갱신은 SSE 로 바뀐 것만 밀어준다.
그래야 스파크라인 60개를 5초마다 다시 보내지 않아도 된다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from devdash.api.payloads import (
    DEFAULT_CHART_POINTS,
    SPARKLINE_POINTS,
    containers_payload,
    samples_payload,
    server_summary,
)
from devdash.collect.docker import logs_command

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

STREAM_TICK_SECONDS = 1.0
"""갱신을 얼마나 자주 확인할지. 수집 주기(5초)보다 촘촘해야 늦지 않게 전달된다."""


def _context(request: Request):
    state = request.app.state
    return state.config, state.scheduler, state.store


def _server_config(config, server_id: str):
    try:
        return config.get(server_id)
    except KeyError as exc:
        # str(KeyError("메시지")) 는 따옴표를 덧씌운다. 원문만 꺼내 쓴다
        detail = exc.args[0] if exc.args else "서버를 찾을 수 없습니다"
        raise HTTPException(status_code=404, detail=detail) from exc


def build_summaries(config, scheduler, store, with_spark: bool) -> list[dict]:
    states = scheduler.states()
    summaries = []
    for cfg in config.servers:
        spark = None
        if with_spark:
            recent = store.samples(cfg.id)[-SPARKLINE_POINTS:]
            spark = [round(s.cpu_percent, 1) if s.cpu_percent is not None else None for s in recent]
        summaries.append(
            server_summary(
                cfg,
                states[cfg.id],
                store.latest_sample(cfg.id),
                store.docker(cfg.id),
                spark=spark,
            )
        )
    return summaries


@router.get("/servers")
def list_servers(request: Request) -> list[dict]:
    """그리드 초기 로드. 카드가 그리는 값과 스파크라인을 함께 준다."""
    config, scheduler, store = _context(request)
    return build_summaries(config, scheduler, store, with_spark=True)


@router.get("/servers/{server_id}/samples")
def get_samples(
    request: Request,
    server_id: str,
    points: int = Query(DEFAULT_CHART_POINTS, ge=1, le=5000),
) -> dict:
    """상세 차트 초기 로드. 보관 중인 샘플을 요청한 점 수만큼 간추려 준다."""
    config, _, store = _context(request)
    cfg = _server_config(config, server_id)
    return samples_payload(cfg.id, store.samples(cfg.id), points)


@router.get("/servers/{server_id}/containers")
def get_containers(request: Request, server_id: str) -> dict:
    config, _, store = _context(request)
    cfg = _server_config(config, server_id)
    return containers_payload(cfg.id, store.docker(cfg.id))


DEFAULT_LOG_TAIL = 200


@router.get("/servers/{server_id}/containers/{container_id}/logs")
async def get_container_logs(
    request: Request,
    server_id: str,
    container_id: str,
    tail: int = Query(DEFAULT_LOG_TAIL, ge=1, le=2000),
) -> dict:
    """컨테이너 로그 마지막 몇 줄.

    수집된 목록에 있는 id 인지 먼저 확인한다. 이 값이 그대로 원격 셸에 들어가므로,
    아무 문자열이나 통과시키면 명령을 끼워 넣을 수 있다.

    수집이 쓰는 SSH 연결을 그대로 빌려 쓴다. 채널이 따로 열려 수집을 막지 않는다.
    """
    config, scheduler, store = _context(request)
    cfg = _server_config(config, server_id)

    snapshot = store.docker(cfg.id)
    known = {c.id: c.name for c in snapshot.containers} if snapshot else {}
    if container_id not in known:
        raise HTTPException(
            status_code=404,
            detail="그 컨테이너를 찾을 수 없습니다. 목록이 갱신되기를 기다렸다 다시 시도하세요.",
        )

    connection = scheduler.connection(cfg.id)
    try:
        text = await connection.run(logs_command(container_id, tail, cfg.docker_sudo))
    except Exception as exc:
        # 명령 실패든 연결 끊김이든 화면에서는 똑같이 "못 가져왔다" 이다
        raise HTTPException(status_code=502, detail=f"로그를 가져오지 못했습니다: {exc}") from exc

    return {
        "server_id": cfg.id,
        "container_id": container_id,
        "name": known[container_id],
        "tail": tail,
        "text": text,
    }


def _event(name: str, data) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_events(config, scheduler, store) -> AsyncIterator[str]:
    """바뀐 것만 내보낸다.

    자원과 컨테이너를 한 이벤트로 묶지 않는 이유는 주기가 다르기 때문이다.
    묶으면 컨테이너 수십 개를 5초마다 다시 보내게 된다.

    샘플 시각뿐 아니라 요약 전체를 비교한다. 서버가 죽어 새 샘플이 안 들어와도
    연결 상태는 바뀌므로, 그 변화는 화면에 전해져야 한다.
    """
    last_summaries: list[dict] | None = None
    last_docker_ts: dict[str, float | None] = {}

    while True:
        summaries = build_summaries(config, scheduler, store, with_spark=False)
        if summaries != last_summaries:
            last_summaries = summaries
            yield _event("update", summaries)

        for cfg in config.servers:
            snapshot = store.docker(cfg.id)
            ts = snapshot.ts if snapshot else None
            if ts is not None and last_docker_ts.get(cfg.id) != ts:
                last_docker_ts[cfg.id] = ts
                yield _event("containers", containers_payload(cfg.id, snapshot))

        await asyncio.sleep(STREAM_TICK_SECONDS)


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    config, scheduler, store = _context(request)
    return StreamingResponse(
        stream_events(config, scheduler, store),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
