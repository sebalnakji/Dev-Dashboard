"""이 PC 의 메모리 · 프로세스 — OS 공통 (psutil).

윈도우 전용 명령(tasklist · taskkill · GlobalMemoryStatusEx)을 쓰던 것을 psutil 하나로 바꿨다.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile

import psutil


def memory_status() -> tuple[int, int]:
    """(전체, 남은) 물리 메모리 바이트."""
    vm = psutil.virtual_memory()
    return vm.total, vm.available


def app_memory() -> dict[str, int]:
    """앱 이름별 메모리 합계 (작업 집합 · RSS). 윈도우 작업 관리자의 '메모리' 열과 같은 기준이다.

    크롬처럼 프로세스가 수십 개인 앱은 따로 보면 순위가 의미 없어 이름으로 합친다.
    읽을 수 없는 프로세스(권한 · 그새 끝남)는 건너뛴다.
    """
    sums: dict[str, int] = {}
    for proc in psutil.process_iter(["name", "memory_info"]):
        name = proc.info.get("name")
        mem = proc.info.get("memory_info")
        if not name or mem is None:
            continue
        name = name.removesuffix(".exe").removesuffix(".EXE")
        sums[name] = sums.get(name, 0) + mem.rss
    return sums


def kill_tree(pid: int) -> None:
    """프로세스와 그 하위 프로세스(MCP 서버 · 셸 등)를 함께 끊는다. 이미 없으면 그냥 끝난다."""
    try:
        parent = psutil.Process(pid)
        procs = [*parent.children(recursive=True), parent]
    except psutil.Error:
        return
    for proc in procs:
        with contextlib.suppress(psutil.Error):
            proc.kill()
    psutil.wait_procs(procs, timeout=5)


def run_bounded(
    args: list[str],
    *,
    timeout: float,
    input_text: str | None = None,
    cwd: str | None = None,
    env: dict | None = None,
) -> tuple[int, str, str] | None:
    """명령을 실행해 (종료 코드, 표준 출력, 표준 오류). timeout 안에 안 끝나면 끄고 None.

    subprocess.run(timeout=…) 은 윈도에서 시간이 넘으면 직계 프로세스만 죽이고, 그 뒤 출력
    파이프가 닫힐 때까지 시간 제한 없이 기다린다. claude 는 cmd → node, git fetch 는 git → ssh
    처럼 한 단계 더 띄우므로 손자가 파이프를 쥔 채 남으면 영영 풀리지 않는다.
    그래서 입출력을 파이프 대신 임시 파일로 주고받고, 시간이 넘으면 **그 PID 의 트리만** 끝낸다
    (이름으로 고르지 않는다 — 사용자의 다른 프로그램을 건드리지 않게).
    """
    extra: dict = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW  # 콘솔 창이 깜빡이지 않게
    with (
        tempfile.TemporaryFile() as stdin,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
    ):
        if input_text is not None:
            stdin.write(input_text.encode("utf-8"))
            stdin.seek(0)
        proc = subprocess.Popen(
            args,
            stdin=stdin if input_text is not None else subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            cwd=cwd,
            env=env,
            **extra,
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            proc.wait()
            return None
        stdout.seek(0)
        stderr.seek(0)
        return (
            code,
            stdout.read().decode("utf-8", errors="replace"),
            stderr.read().decode("utf-8", errors="replace"),
        )
