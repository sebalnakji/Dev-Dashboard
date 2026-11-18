"""start · stop 테스트 — 내가 띄운 devdash 만 끄고, 남이 쓰는 포트는 건드리지 않는다.

실제 서버를 띄우지 않는다. 포트에는 작은 HTTP 서버를 붙여 'devdash' 나 '다른 프로그램' 흉내를 낸다.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from devdash import launch


class _Fake(BaseHTTPRequestHandler):
    health: dict | None = None  # None 이면 404 (devdash 가 아닌 프로그램)

    def do_GET(self):  # noqa: N802 — http.server 가 정한 이름
        if self.path == "/api/health" and self.health is not None:
            body = json.dumps(self.health).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def serve_fake():
    """serve_fake(health) → 포트. health 가 None 이면 devdash 가 아닌 프로그램."""
    servers = []

    def start(health: dict | None) -> int:
        handler = type("H", (_Fake,), {"health": health})
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path))
    killed: list[int] = []
    monkeypatch.setattr(launch.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(launch, "STOP_WAIT", 0.5)

    def no_spawn(port):
        raise AssertionError("띄우면 안 된다")

    monkeypatch.setattr(launch, "_spawn_server", no_spawn)
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: None)
    return {"dir": tmp_path, "killed": killed}


def _state(home, pid, port):
    (home["dir"] / "server.json").write_text(json.dumps({"pid": pid, "port": port}))


def test_health_는_devdash_와_PID_를_댄다():
    import os

    from fastapi.testclient import TestClient

    from devdash.app import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        assert client.get("/api/health").json() == {"app": "devdash", "pid": os.getpid()}


def test_포트를_다른_프로그램이_쓰면_끄지_않고_멈춘다(home, serve_fake, capsys):
    port = serve_fake(None)
    assert launch.start(port) == 1
    assert "다른 프로그램" in capsys.readouterr().out
    assert home["killed"] == [] and not (home["dir"] / "server.json").exists()


def test_이미_띄운_devdash_가_있으면_새로_띄우지_않는다(home, serve_fake, capsys):
    port = serve_fake({"app": "devdash", "pid": 4321})
    _state(home, 4321, port)
    assert launch.start(port) == 0
    assert "이미 실행 중" in capsys.readouterr().out


def test_stop_은_기록한_PID_가_그_포트의_devdash_일_때만_끈다(home, serve_fake):
    port = serve_fake({"app": "devdash", "pid": 4321})
    _state(home, 4321, port)
    launch.stop()  # 가짜 서버는 꺼지지 않으므로 '안 꺼졌다'로 끝나지만 끄기는 시도한다
    assert home["killed"] == [4321]


def test_stop_은_PID_가_다르면_끄지_않는다(home, serve_fake):
    """남은 기록의 PID 가 다른 프로그램에 다시 쓰인 경우 — 포트의 devdash 는 다른 PID 를 댄다."""
    port = serve_fake({"app": "devdash", "pid": 9999})
    _state(home, 4321, port)
    assert launch.stop() == 0
    assert home["killed"] == []
    assert not (home["dir"] / "server.json").exists()


def test_stop_은_포트가_devdash_가_아니면_끄지_않는다(home, serve_fake):
    port = serve_fake(None)
    _state(home, 4321, port)
    assert launch.stop() == 0
    assert home["killed"] == []


def test_띄운_기록이_없으면_아무것도_끄지_않는다(home, capsys):
    assert launch.stop() == 0
    assert "없습니다" in capsys.readouterr().out
    assert home["killed"] == []


def test_start_는_서버가_댄_PID_를_기록한다(home, serve_fake, monkeypatch):
    """윈도우 가상환경의 python.exe 는 중계 프로그램이라 띄운 프로세스 PID 와 서버 PID 가 다르다."""
    port = serve_fake({"app": "devdash", "pid": 777})
    monkeypatch.setattr(launch, "port_in_use", lambda p: False)
    # 화면 빌드본이 없으면 빌드부터 한다 — 저장소에 빌드본이 있는지에 기대지 않게
    repo = home["dir"] / "repo"
    (repo / "src" / "frontend" / "dist").mkdir(parents=True)
    (repo / "src" / "frontend" / "dist" / "index.html").write_text("")
    monkeypatch.setattr(launch, "REPO", repo)

    class Proc:
        pid = 111

        def poll(self):
            return None

    monkeypatch.setattr(launch, "_spawn_server", lambda p: Proc())
    assert launch.start(port, open_browser=False) == 0
    assert launch.read_state() == {"pid": 777, "port": port}
