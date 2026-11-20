"""이 PC 안의 화면만 API 를 쓰게 막기."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devdash.api import guard


def make() -> TestClient:
    app = FastAPI()
    guard.install(app)

    @app.get("/x")
    def read():
        return {"ok": True}

    @app.post("/x")
    def change():
        return {"ok": True}

    return TestClient(app, base_url="http://127.0.0.1:8770")


def test_이_PC_주소로_온_요청은_통과한다():
    c = make()
    assert c.get("/x").status_code == 200
    assert c.get("/x", headers={"host": "localhost:5173"}).status_code == 200  # 개발 서버 경유
    assert c.post("/x", headers={"origin": "http://localhost:8770"}).status_code == 200
    assert c.post("/x").status_code == 200  # Origin 이 없는 요청(curl 등)


def test_다른_이름으로_온_요청은_막는다_DNS_리바인딩():
    c = make()
    r = c.get("/x", headers={"host": "evil.example:8770"})
    assert r.status_code == 403 and "127.0.0.1" in r.json()["error"]


def test_다른_사이트에서_보낸_POST_는_막는다():
    c = make()
    assert c.post("/x", headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/x", headers={"origin": "null"}).status_code == 403  # 샌드박스 iframe 등
    # 읽기는 막지 않는다 — 다른 사이트가 응답을 읽는 것은 브라우저의 CORS 가 막는다
    assert c.get("/x", headers={"origin": "https://evil.example"}).status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "LOCALHOST"])
def test_serve_는_이_PC_안의_주소로만_듣는다(host):
    from devdash.__main__ import loopback

    assert loopback(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.0.10", "::", "example.com", ""])
def test_다른_PC_에서_닿는_주소는_거절한다(host, capsys):
    from devdash.__main__ import main

    with pytest.raises(SystemExit) as stop:
        main(["serve", "--host", host])
    assert stop.value.code == 2 and "이 PC 안의 주소만" in capsys.readouterr().err
