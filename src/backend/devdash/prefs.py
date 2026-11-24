"""setup 때 고른 것 (~/.devdash/settings.json).

- shortcut: setup 이 만든 바탕화면 바로가기 경로 — uninstall 은 이것만 지운다
- autoCompat: Claude Code 버전이 바뀌면 대시보드가 호환 시험을 스스로 돌릴지
  (시험 세션 + haiku 질문 2번)
"""

from __future__ import annotations

import json

from devdash.home import home


def path():
    return home() / "settings.json"


def load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get(key: str, default=None):
    return load().get(key, default)


def put(key: str, value) -> None:
    """한 항목을 바꾼다. None 이면 지운다."""
    data = load()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
