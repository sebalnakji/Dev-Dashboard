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
    # 이 폴더의 실제 git 상태 대신 — 시험마다 정한다
    monkeypatch.setattr(launch.codestate, "current", lambda: dict(CODE))
    monkeypatch.setattr(launch.codestate, "frontend_build_needed", lambda now=None: (False, False))
    return {"dir": tmp_path, "killed": killed}


CODE = {"commit": "c1", "clean": True}


def _state(home, pid, port):
    (home["dir"] / "server.json").write_text(json.dumps({"pid": pid, "port": port}))


def test_health_는_devdash_와_PID_를_댄다():
    import os

    from fastapi.testclient import TestClient

    from devdash.app import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        health = client.get("/api/health").json()
        assert health["app"] == "devdash" and health["pid"] == os.getpid()
        # 띄워질 때의 코드 상태 — start 가 지금 코드와 견준다
        assert set(health["code"]) == {"commit", "clean"}


def test_포트를_다른_프로그램이_쓰면_끄지_않고_멈춘다(home, serve_fake, capsys):
    port = serve_fake(None)
    assert launch.start(port) == 1
    assert "다른 프로그램" in capsys.readouterr().out
    assert home["killed"] == [] and not (home["dir"] / "server.json").exists()


def test_이미_띄운_devdash_가_있으면_새로_띄우지_않는다(home, serve_fake, capsys):
    port = serve_fake({"app": "devdash", "pid": 4321, "code": CODE})
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


def test_서버를_띄우면_세션_줄을_미리_읽는다(monkeypatch):
    """첫 화면이 claude 목록 · 대화 기록 요약을 기다리지 않게 (처음엔 몇 초 걸렸다)."""
    from devdash import app as app_mod

    warmed = []
    monkeypatch.setattr(app_mod.SessionService, "rows", lambda self: warmed.append("rows") or [])
    app_mod._warm_up(app_mod.SessionService())
    assert warmed == ["rows"]


# --- 코드를 합친 뒤 아이콘(start)만 눌러도 반영되게 ---


@pytest.fixture
def restartable(home, serve_fake, monkeypatch):
    """끄면 포트가 비고(404), 다시 띄우면 새 PID 로 답하는 가짜 devdash."""
    holder = {}

    def start(code):
        health = {"app": "devdash", "pid": 4321}
        if code is not None:
            health["code"] = code  # None: 이 기능 전의 서버 — 코드를 대지 않는다
        port = serve_fake(health)
        # 같은 dict 를 쥔 가짜 처리기 (다른 시험이 만든 것과 헷갈리지 않게 dict 자체로 찾는다)
        holder["handler"] = next(h for h in _Fake.__subclasses__() if h.health is health)
        _state(home, 4321, port)
        return port

    def kill(pid, sig):
        home["killed"].append(pid)
        holder["handler"].health = None  # 꺼짐

    spawned = []

    def spawn(port):
        spawned.append(port)
        holder["handler"].health = {"app": "devdash", "pid": 5555, "code": CODE}
        return type("P", (), {"poll": lambda self: None})()

    monkeypatch.setattr(launch.os, "kill", kill)
    monkeypatch.setattr(launch, "_spawn_server", spawn)
    return {"start": start, "spawned": spawned}


@pytest.mark.parametrize(
    "running",
    [
        {"commit": "c0", "clean": True},  # 합친 뒤 — 커밋이 다르다
        {"commit": "c1", "clean": False},  # 고친 채로 떠 있었다 — 같은지 알 수 없다
        None,  # 이 기능 전의 서버 — 코드를 대지 않는다
    ],
)
def test_떠_있는_서버가_지금_코드와_다르면_끄고_새로_띄운다(home, restartable, capsys, running):
    port = restartable["start"](running)
    assert launch.start(port) == 0
    out = capsys.readouterr().out
    assert "새 코드로 다시 띄웁니다" in out
    assert home["killed"] == [4321] and restartable["spawned"] == [port]
    assert json.loads((home["dir"] / "server.json").read_text())["pid"] == 5555


def test_화면_빌드가_실패하면_떠_있던_서버를_그대로_둔다(home, restartable, capsys, monkeypatch):
    port = restartable["start"]({"commit": "c0", "clean": True})
    monkeypatch.setattr(launch.codestate, "frontend_build_needed", lambda now=None: (True, False))
    monkeypatch.setattr(launch, "_build", lambda deps: False)
    assert launch.start(port) == 1
    assert home["killed"] == [] and restartable["spawned"] == []
    assert "그대로 둡니다" in capsys.readouterr().out


def test_같은_코드라도_화면_코드가_바뀌었으면_빌드하고_다시_띄운다(home, restartable, monkeypatch):
    port = restartable["start"](CODE)
    built = []
    monkeypatch.setattr(launch.codestate, "frontend_build_needed", lambda now=None: (True, True))
    monkeypatch.setattr(launch, "_build", lambda deps: built.append(deps) or True)
    assert launch.start(port) == 0
    assert built == [True] and restartable["spawned"] == [port]
