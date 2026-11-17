"""FastAPI 앱 조립.

수집 스케줄러를 앱 수명주기에 매단다. 앱이 뜨면 수집이 시작되고,
내려가면 SSH 연결까지 정리된다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from devdash.api import guard
from devdash.api.claude_routes import router as claude_router
from devdash.api.project_routes import router as project_router
from devdash.api.routes import router
from devdash.claude.service import SessionService
from devdash.collect.scheduler import Scheduler
from devdash.config import DEFAULT_CONFIG_PATH, load_config
from devdash.store.memory import MemoryStore

logger = logging.getLogger(__name__)


def create_app(config_path: Path = DEFAULT_CONFIG_PATH) -> FastAPI:
    config = load_config(config_path)
    store = MemoryStore()
    scheduler = Scheduler(config, store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    app = FastAPI(title="devdash", lifespan=lifespan)
    app.state.config = config
    app.state.store = store
    app.state.scheduler = scheduler
    app.state.claude = SessionService()
    # 다른 웹 페이지가 이 API 를 부르지 못하게 (DNS 리바인딩 · 다른 사이트의 POST)
    guard.install(app)
    app.include_router(router)
    app.include_router(claude_router)
    app.include_router(project_router)

    # 빌드된 프론트엔드를 백엔드가 직접 서빙한다. API 라우터 뒤에 "/" 로 붙이므로
    # /api/* 는 위에서 먼저 잡히고 나머지는 정적 파일로 간다. 화면은 해시 라우팅이라
    # index.html 하나면 충분하다(html=True).
    # 이유: Vite 개발 서버(5173)가 세션 종료·좀비 프로세스로 자꾸 죽어 화면이 끊겼다.
    # 이제 프로세스 하나(백엔드)가 API 와 화면을 같은 주소(8770)에서 함께 준다.
    # dist 가 없으면(빌드 전) 건너뛴다 — 개발 땐 vite dev 서버를 쓴다.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    else:
        logger.warning("프론트 빌드본이 없다 (%s). API 만 서빙한다.", dist)
    return app
