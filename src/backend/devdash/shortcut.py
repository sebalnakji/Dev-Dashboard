"""바탕화면 바로가기 — setup 이 묻고 만들고, uninstall 은 setup 이 만든 것만 지운다.

- 윈도우: `Dev Dashboard.lnk` → start.bat (PowerShell 의 WScript.Shell 로 만든다)
- 리눅스: `dev-dashboard.desktop` (실행 권한, 바탕화면 폴더는 xdg-user-dir 로)
- macOS: `Dev Dashboard.command` (실행 권한 — 누르면 터미널이 start.sh 를 돌린다)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from devdash import prefs
from devdash.home import REPO

NAME = "Dev Dashboard"


def desktop_dir() -> Path | None:
    """이 사용자의 바탕화면 폴더. 못 찾으면 None."""
    if sys.platform == "win32":
        # OneDrive 로 옮긴 바탕화면도 맞게 OS 에 묻는다
        try:
            out = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "[Environment]::GetFolderPath('Desktop')",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        found = Path(out) if out else Path.home() / "Desktop"
    elif sys.platform == "darwin":
        found = Path.home() / "Desktop"
    else:
        out = ""
        if shutil.which("xdg-user-dir"):
            try:
                out = subprocess.run(
                    ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=10
                ).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                out = ""
        found = Path(out) if out else Path.home() / "Desktop"
    return found if found.is_dir() else None


def target(desktop: Path) -> Path:
    """이 OS 에서 만들 바로가기 파일 경로."""
    if sys.platform == "win32":
        return desktop / f"{NAME}.lnk"
    if sys.platform == "darwin":
        return desktop / f"{NAME}.command"
    return desktop / "dev-dashboard.desktop"


def existing() -> Path | None:
    """setup 이 만들어 둔 바로가기 (아직 있을 때만)."""
    recorded = prefs.get("shortcut")
    return Path(recorded) if recorded and Path(recorded).exists() else None


def create(desktop: Path) -> Path:
    """바로가기를 만들고 그 경로를 적어 둔다. 실패하면 OSError."""
    path = target(desktop)
    if sys.platform == "win32":
        start = REPO / "start.bat"
        script = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:DD_LNK); "
            "$s.TargetPath = $env:DD_TARGET; $s.WorkingDirectory = $env:DD_DIR; "
            "$s.Description = 'Dev Dashboard'; $s.Save()"
        )
        env = {**os.environ, "DD_LNK": str(path), "DD_TARGET": str(start), "DD_DIR": str(REPO)}
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        if r.returncode != 0 or not path.exists():
            raise OSError(r.stderr.strip() or "바로가기를 만들지 못했습니다")
    elif sys.platform == "darwin":
        path.write_text(f'#!/bin/sh\nexec "{REPO / "start.sh"}"\n', encoding="utf-8")
        path.chmod(0o755)
    else:
        path.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={NAME}\n"
            "Comment=원격 서버 · Claude Code 세션 · 이 PC 메모리\n"
            f'Exec="{REPO / "start.sh"}"\n'
            f"Path={REPO}\n"
            "Terminal=false\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        if shutil.which("gio"):
            # GNOME 은 이 표시가 없으면 바탕화면 아이콘을 실행하지 않는다
            subprocess.run(
                ["gio", "set", str(path), "metadata::trusted", "true"],
                capture_output=True,
                timeout=10,
            )
    prefs.put("shortcut", str(path))
    return path


def remove() -> Path | None:
    """setup 이 만든 바로가기만 지운다 (적어 둔 경로). 지운 경로, 없으면 None."""
    recorded = prefs.get("shortcut")
    if not recorded:
        return None
    path = Path(recorded)
    if path.exists():
        path.unlink()
    prefs.put("shortcut", None)
    return path
