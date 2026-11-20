"""윈도우에서만 되는 동작: 터미널 창 열기 · 폴더 선택 창.

메모리 · 프로세스는 OS 공통(system.py, psutil)으로 옮겼다. 다른 OS 에서는 이 두 버튼을
숨긴다 (local.ABILITIES).
"""

from __future__ import annotations

import subprocess


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
