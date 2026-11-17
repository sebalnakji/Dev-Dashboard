"""devdash 플러그인이 쓰는 작업 기록 폴더(~/.devdash)를 Claude Code 가 묻지 않고 읽고 쓰게 한다.

할 일 파일은 저장소 밖에 있어서(ADR 0007) Claude Code 가 파일마다 허락을 묻는다. 백그라운드
세션은 답할 사람이 없어 그 자리에서 멈춘다 (5단계 실제 세션으로 확인). 플러그인은 권한 규칙을
스스로 실어 보낼 수 없으므로(공식 문서) 사용자 설정(~/.claude/settings.json)의
permissions.allow 에 규칙 두 줄을 넣는다 (ADR 0008). setup 이 부른다.

    python -m devdash plugin allow
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"


def devdash_home() -> Path:
    """플러그인의 devdash_store.home() 과 같은 위치."""
    return Path(os.environ.get("DEVDASH_HOME") or Path.home() / ".devdash")


def rules(home: Path) -> list[str]:
    """권한 규칙 두 줄. 윈도우 경로는 규칙에서 '//c/Users/...' 로 쓴다 (공식 문서).
    Edit 규칙이 새 파일 만들기(Write)까지 덮는다 (실제 세션으로 확인).
    정션을 풀지 않는다 — Claude 는 훅이 알려 준 경로(풀지 않은 것)로 접근한다."""
    posix = home.absolute().as_posix()  # 'C:/Users/<사용자>/.devdash'
    if len(posix) > 1 and posix[1] == ":":
        posix = f"//{posix[0].lower()}{posix[2:]}"
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


def allow(path: Path = SETTINGS) -> str:
    """설정 파일에 규칙을 넣고 결과 한 줄을 돌려준다. 이미 있으면 파일을 건드리지 않는다.
    읽을 수 없거나 모양이 다른 파일이면 고치지 않고 ValueError.

    처음 고칠 때만 원본을 '.devdash-backup' 으로 남긴다 (다시 고쳐도 첫 원본을 덮지 않는다).
    """
    text = path.read_text(encoding="utf-8-sig") if path.exists() else "{}"
    try:
        settings = json.loads(text or "{}")
    except ValueError as exc:
        raise ValueError(f"{path} 를 JSON 으로 읽지 못해 고치지 않았습니다: {exc}") from None
    if not isinstance(settings, dict):
        raise ValueError(f"{path} 의 내용이 설정(객체)이 아니라 고치지 않았습니다")
    added = add_rules(settings, rules(devdash_home()))
    if not added:
        return "이미 허용되어 있습니다"
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
    return f"허용 규칙을 넣었습니다: {', '.join(added)}"
