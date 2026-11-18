"""대시보드를 뒤에서 띄우고(start) 끈다(stop).

    devdash start [--port 8770]   뒤에서 serve 를 띄우고 브라우저를 연다
    devdash stop                  start 로 띄운 것만 끈다

**내가 띄운 것만 끈다.** start 는 띄운 서버의 PID 와 포트를 `~/.devdash/server.json` 에 적고,
stop 은 그 포트의 `/api/health` 가 같은 PID 를 대는 경우에만 끈다. 그래야

- 꺼진 뒤 남은 파일의 PID 가 다른 프로그램에 다시 쓰였어도 그 프로그램을 끄지 않고
- 같은 포트를 쓰는 남의 프로그램(다른 개발 서버 등)을 끄지 않는다

start 때 포트를 다른 프로그램이 쓰고 있으면 끄지 않고 알린 뒤 멈춘다.

PID 는 띄운 프로세스가 아니라 서버가 스스로 댄 것을 적는다. 윈도우의 가상환경 python.exe 는
진짜 파이썬을 자식으로 한 번 더 띄우는 중계 프로그램이라,
띄운 프로세스의 PID 로는 서버를 끌 수 없다.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from devdash.home import REPO, home, log_dir

DEFAULT_PORT = 8770
START_WAIT = 60.0
"""초. 서버가 처음 뜰 때 수집기 · 화면 파일을 준비하느라 몇 초 걸린다."""
STOP_WAIT = 10.0


def state_path() -> Path:
    return home() / "server.json"


# --- 확인 ----------------------------------------------------------------------


def devdash_pid(port: int) -> int | None:
    """이 포트에서 devdash 가 답하면 그 서버의 PID, 아니면 None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as res:
            data = json.loads(res.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if isinstance(data, dict) and data.get("app") == "devdash" and isinstance(data.get("pid"), int):
        return data["pid"]
    return None


def port_in_use(port: int) -> bool:
    """무언가 이 포트에서 연결을 받는지."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def read_state() -> dict | None:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("pid"), int)
        and isinstance(data.get("port"), int)
    ):
        return data
    return None


def _write_state(pid: int, port: int) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "port": port}) + "\n", encoding="utf-8")


def _clear_state() -> None:
    state_path().unlink(missing_ok=True)


# --- start ---------------------------------------------------------------------


def start(port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    url = f"http://127.0.0.1:{port}/"
    state = read_state()
    if state:
        if devdash_pid(state["port"]) == state["pid"]:
            running = f"http://127.0.0.1:{state['port']}/"
            print(f"이미 실행 중입니다: {running}")
            if open_browser:
                webbrowser.open(running)
            return 0
        _clear_state()  # 지난번에 꺼진 뒤 남은 기록

    if port_in_use(port):
        who = "다른 곳에서 띄운 devdash 가" if devdash_pid(port) else "다른 프로그램이"
        print(
            f"포트 {port} 를 {who} 쓰고 있어 시작하지 않았습니다"
            " (그 프로그램은 끄지 않습니다).\n"
            "그 프로그램을 끄거나, 다른 포트로 시작하세요: devdash start --port <번호>"
        )
        return 1

    if not (REPO / "src" / "frontend" / "dist" / "index.html").exists():
        # 처음이거나 빌드본을 지운 뒤. node 가 필요하다
        from devdash.install import build_frontend

        print("화면 빌드본이 없어 빌드합니다.")
        if not build_frontend(clean_install=False):
            print("화면을 빌드하지 못했습니다. setup 을 다시 실행하세요.")
            return 1

    proc = _spawn_server(port)
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline:
        pid = devdash_pid(port)
        if pid is not None:
            _write_state(pid, port)
            print(f"대시보드: {url}")
            if open_browser:
                webbrowser.open(url)
            return 0
        if proc.poll() is not None:
            break  # 뜨다가 죽었다 (설정 오류 등)
        time.sleep(0.4)
    print(f"대시보드가 뜨지 않았습니다. 로그를 확인하세요: {log_dir() / 'devdash.log'}")
    return 1


def _spawn_server(port: int) -> subprocess.Popen:
    """serve 를 이 창과 떨어진 프로세스로 띄운다. 이 명령(과 창)이 끝나도 서버는 남는다.

    윈도우는 창 없는 콘솔을 준다(CREATE_NO_WINDOW). 서버가 부르는 git · claude 가 저마다 창을
    띄우지 않게, 콘솔이 아예 없는 DETACHED_PROCESS 는 쓰지 않는다.
    """
    args = [sys.executable, "-m", "devdash", "serve", "--port", str(port)]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(home()) if home().is_dir() else None,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


# --- stop ----------------------------------------------------------------------


def stop() -> int:
    state = read_state()
    if state is None:
        print("start 로 띄운 대시보드가 없습니다.")
        return 0
    pid, port = state["pid"], state["port"]
    if devdash_pid(port) != pid:
        # 이미 꺼졌다. 그 PID 가 다른 프로그램에 다시 쓰였을 수 있으니 끄지 않는다
        _clear_state()
        print("대시보드는 이미 꺼져 있습니다.")
        return 0

    try:
        os.kill(pid, signal.SIGTERM)  # 윈도우는 TerminateProcess
    except OSError as exc:
        print(f"대시보드(PID {pid})를 끄지 못했습니다: {exc}")
        return 1
    deadline = time.monotonic() + STOP_WAIT
    while time.monotonic() < deadline:
        if devdash_pid(port) is None:
            _clear_state()
            print("대시보드를 껐습니다.")
            return 0
        time.sleep(0.3)
    print(f"대시보드(PID {pid})가 {STOP_WAIT:.0f}초 안에 꺼지지 않았습니다.")
    return 1
