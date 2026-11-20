"""이 PC 안의 화면만 API 를 쓰게 막는다 (T-13, 8단계 Fable 검토).

대시보드는 127.0.0.1 에서만 듣지만, 그것만으로는 다른 웹 페이지를 막지 못한다.

- **DNS 리바인딩**: 악성 페이지가 자기 도메인의 주소를 127.0.0.1 로 바꾸면 브라우저는 그 페이지와
  이 대시보드를 같은 출처로 본다. 그러면 파일 · 대화 · diff 를 읽어 갈 수 있다. 이때 요청의 Host 는
  그 도메인 이름이므로, Host 가 이 PC 주소가 아니면 거절한다
- **다른 사이트의 POST**: 본문 없는 POST 는 브라우저가 미리 묻지 않고 보낸다. 사람이 누르지 않았는데
  커밋 요약(코드가 Anthropic 으로 나간다)이나 fetch 가 돌 수 있다. 상태를 바꾸는 요청은 Origin 이
  있으면 이 PC 주소인지 본다 (Origin 이 없는 요청은 브라우저 밖 — curl 등 — 이라 막지 않는다)

`--host` 도 이 PC 안의 주소(LOCAL)만 받는다 — 다른 PC 에서 닿는 주소로는 듣지 않는다.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LOCAL = {"127.0.0.1", "localhost", "::1"}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(netloc: str) -> str:
    """'127.0.0.1:8770' · '[::1]:8770' · 'localhost' → 포트를 뗀 이름 (소문자)."""
    return (urlsplit(f"//{netloc}").hostname or "").lower()


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def only_this_pc(request: Request, call_next):
        if _hostname(request.headers.get("host", "")) not in LOCAL:
            return JSONResponse(
                {"error": "이 PC 의 주소(127.0.0.1)로만 열 수 있습니다"}, status_code=403
            )
        origin = request.headers.get("origin")
        changes = request.method not in SAFE_METHODS
        if changes and origin is not None and _hostname(urlsplit(origin).netloc) not in LOCAL:
            return JSONResponse({"error": "다른 사이트에서 보낸 요청입니다"}, status_code=403)
        return await call_next(request)
