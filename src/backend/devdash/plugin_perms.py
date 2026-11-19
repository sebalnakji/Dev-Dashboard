"""devdash 플러그인이 쓰는 작업 기록 폴더(~/.devdash)를 Claude Code 가 묻지 않고 읽고 쓰게 한다.

할 일 파일은 저장소 밖에 있어서 Claude Code 가 파일마다 허락을 묻는다. 백그라운드 세션은 답할
사람이 없어 그 자리에서 멈춘다 (실제 세션으로 확인). 플러그인은 권한 규칙을 스스로 실어 보낼 수
없으므로(공식 문서) 사용자 설정(<Claude 설정 폴더>/settings.json)의 permissions.allow 에 규칙 두
줄을 넣는다. setup 이 넣고 uninstall 이 뺀다.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from devdash.home import claude_dir, home


def settings_path() -> Path:
    return claude_dir() / "settings.json"


def rules(home: Path) -> list[str]:
    """권한 규칙 두 줄. 절대 경로는 '//' 로 시작해야 한다 (공식 문서) — '/' 하나면 루트가 아니라
    설정 파일 자리(~/.claude/) 기준이 된다. 윈도우 'C:/Users/…' → '//c/Users/…',
    macOS · Linux '/home/…' → '//home/…'.
    Edit 규칙이 새 파일 만들기(Write)까지 덮는다 (실제 세션으로 확인).
    정션을 풀지 않는다 — Claude 는 훅이 알려 준 경로(풀지 않은 것)로 접근한다."""
    posix = home.absolute().as_posix()  # 'C:/Users/<사용자>/.devdash'
    if len(posix) > 1 and posix[1] == ":":
        posix = f"//{posix[0].lower()}{posix[2:]}"
    elif posix.startswith("/") and not posix.startswith("//"):
        posix = "/" + posix
    return [f"Read({posix}/**)", f"Edit({posix}/**)"]


def add_rules(settings: dict, wanted: list[str]) -> list[str]:
    """settings 의 permissions.allow 에 없는 규칙을 더하고, 더한 것을 돌려준다.
    모양이 예상과 다르면(permissions 가 객체가 아님 등) 고치지 않고 ValueError."""
    perms = settings.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise ValueError("permissions 가 객체가 아니라 고치지 않았습니다")
    allow = perms.setdefault("allow", [])
    if not isinstance(allow, list):
        raise ValueError("permissions.allow 가 목록이 아니라 고치지 않았습니다")
    added = [r for r in wanted if r not in allow]
    allow.extend(added)
    return added


def remove_rules(settings: dict, unwanted: list[str]) -> list[str]:
    """permissions.allow 에서 규칙을 빼고, 뺀 것을 돌려준다. 비게 된 allow · permissions 는 치운다 —
    setup 전에 없던 칸이 빈 채로 남지 않게. 모양이 다르면 손대지 않는다(뺀 것 없음)."""
    perms = settings.get("permissions")
    allow = perms.get("allow") if isinstance(perms, dict) else None
    if not isinstance(allow, list):
        return []
    removed = [r for r in allow if r in unwanted]
    if not removed:
        return []
    perms["allow"] = [r for r in allow if r not in unwanted]
    if not perms["allow"]:
        del perms["allow"]
    if not perms:
        del settings["permissions"]
    return removed


def has_rules(path: Path | None = None) -> bool:
    """규칙 두 줄이 이미 다 있는지. 읽을 수 없으면 없다고 본다."""
    try:
        settings = _read(path or settings_path())
    except ValueError:
        return False
    allow = (settings.get("permissions") or {}).get("allow") or []
    return isinstance(allow, list) and all(r in allow for r in rules(home()))


def allow(path: Path | None = None) -> str:
    """설정 파일에 규칙을 넣고 결과 한 줄을 돌려준다. 이미 있으면 파일을 건드리지 않는다.
    읽을 수 없거나 모양이 다른 파일이면 고치지 않고 ValueError.

    처음 고칠 때만 원본을 '.devdash-backup' 으로 남긴다 (다시 고쳐도 첫 원본을 덮지 않는다).
    """
    path = path or settings_path()
    settings = _read(path)
    added = add_rules(settings, rules(home()))
    if not added:
        return "이미 허용되어 있습니다"
    write_settings(path, settings)
    return f"허용 규칙을 넣었습니다: {', '.join(added)}"


def disallow(path: Path | None = None) -> str:
    """allow 로 넣은 규칙을 뺀다. 없으면 파일을 건드리지 않는다."""
    path = path or settings_path()
    if not path.exists():
        return "허용 규칙이 없습니다"
    settings = _read(path)
    if not remove_rules(settings, rules(home())):
        return "허용 규칙이 없습니다"
    write_settings(path, settings)
    return "허용 규칙을 뺐습니다"


def _read(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig") if path.exists() else "{}"
    try:
        settings = json.loads(text or "{}")
    except ValueError as exc:
        raise ValueError(f"{path} 를 JSON 으로 읽지 못해 고치지 않았습니다: {exc}") from None
    if not isinstance(settings, dict):
        raise ValueError(f"{path} 의 내용이 설정(객체)이 아니라 고치지 않았습니다")
    return settings


def write_settings(path: Path, settings: dict) -> None:
    """통째로 바꿔 끼운다. 처음 고칠 때만 원본을 백업한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".devdash-backup")
    if path.exists() and not backup.exists():
        shutil.copyfile(path, backup)  # 읽기 전용 같은 속성은 옮기지 않는다
    tmp = path.with_name(path.name + ".devdash-tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
