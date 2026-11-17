"""윈도우 전용 동작.

메모리 읽기 · 앱별 메모리 · 프로세스 트리 종료 · 터미널 창 열기를 여기에만 둔다.
다른 OS 가 필요해지면 같은 이름의 함수를 가진 파일을 하나 더 만들고
devdash/local/__init__.py 에서 고르게 하면 된다 (ADR 0005).
"""

from __future__ import annotations

import csv
import ctypes
import io
import subprocess
import tempfile


class _MemStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_status() -> tuple[int, int]:
    """(전체, 남은) 물리 메모리 바이트."""
    st = _MemStatus()
    st.dwLength = ctypes.sizeof(_MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return st.ullTotalPhys, st.ullAvailPhys


def parse_tasklist(text: str) -> dict[str, int]:
    """`tasklist /FO CSV /NH` 출력을 앱 이름별 메모리(작업 집합) 합계로 묶는다.

    크롬처럼 프로세스가 수십 개인 앱은 따로 보면 순위가 의미 없어 이름으로 합친다.
    """
    sums: dict[str, int] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 5:
            continue
        name = row[0].removesuffix(".exe").removesuffix(".EXE")
        # "123,456 K" — 구분 기호는 지역 설정마다 달라 숫자만 뽑는다
        kb = int("".join(ch for ch in row[4] if ch.isdigit()) or 0)
        sums[name] = sums.get(name, 0) + kb * 1024
    return sums


def app_memory() -> dict[str, int]:
    """앱 이름별 메모리 합계. 작업 관리자의 '메모리' 열과 같은 기준이다."""
    r = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    return parse_tasklist(r.stdout.decode("oem", errors="replace"))


def kill_tree(pid: int) -> None:
    """프로세스와 그 하위 프로세스(MCP 서버·셸 등)를 함께 끊는다."""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
    )


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
    처럼 한 단계 더 띄우므로 손자가 파이프를 쥔 채 남으면 영영 풀리지 않는다 (8단계 Fable 검토).
    그래서 입출력을 파이프 대신 임시 파일로 주고받고, 시간이 넘으면 **그 PID 의 트리만** 끝낸다
    (이름으로 고르지 않는다 — 사용자의 다른 프로그램을 건드리지 않게).
    """
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
            creationflags=subprocess.CREATE_NO_WINDOW,  # 콘솔 창이 깜빡이지 않게
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


def open_terminal(cwd: str, title: str, args: list[str]) -> None:
    """Windows Terminal 새 창에서 명령을 실행한다. 명령이 끝나면 창도 닫힌다.

    claude 는 npm 이 깐 .cmd 라 cmd /c 로 띄운다.
    """
    subprocess.Popen(
        ["wt.exe", "-w", "new", "-d", cwd, "--title", title, "cmd", "/c", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


PICK_FOLDER_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{
    TopMost = $true; ShowInTaskbar = $false; Opacity = 0; StartPosition = 'CenterScreen'
}
$owner.Show(); $owner.Activate()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '프로젝트 폴더를 고르세요'
$dialog.ShowNewFolderButton = $false
$result = $dialog.ShowDialog($owner)
$owner.Close()
if ($result -eq 'OK') {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    [Console]::Out.Write($dialog.SelectedPath)
}
"""
"""윈도우 폴더 선택 창. 보이지 않는 맨 위 창을 주인으로 붙여 브라우저 뒤에 숨지 않게 한다."""

PICK_FOLDER_TIMEOUT = 600
"""초. 사람이 고를 때까지 기다린다."""


def pick_folder() -> str | None:
    """이 PC 화면에 폴더 선택 창을 띄우고 고른 경로를 돌려준다. 취소하면 None.
    대시보드는 이 PC 에서만 열리므로(127.0.0.1) 창이 보는 사람 앞에 뜬다."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", PICK_FOLDER_SCRIPT],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=PICK_FOLDER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None
    path = r.stdout.decode("utf-8", errors="replace").strip()
    return path or None
