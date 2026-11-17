"""/clear 로 바뀐 대화 번호를 세션 목록에 자동으로 반영하는 Claude Code 훅.

/clear 는 같은 세션에서 대화 번호만 새로 바꾼다. 목록이 옛 번호를 들고 있으면 재부팅 후
/clear 이전 대화가 되살아나므로, 그 순간 훅으로 번호를 갈아 끼운다.

  SessionEnd(reason=clear)   → 옛 번호를 작업 폴더 기준으로 맡겨 둔다
  SessionStart(source=clear) → 같은 폴더의 맡긴 번호를 찾아, 목록에 있으면 새 번호로 바꾼다

새 대화 시작 훅은 새 번호만 알려주므로 옛 번호와 짝지으려면 두 훅이 다 필요하다.
훅은 모든 세션의 /clear 에 끼어들므로, 부르는 쪽(commands.py)이 어떤 오류도 삼킨다.

알려진 한계: 짝을 작업 폴더로 맞추므로, **같은 폴더의 두 세션이 60초 안에 둘 다 /clear 하면**
번호가 엇갈려 들어갈 수 있다 (예: 같은 폴더의 api-head · api-sub). 드문 경우라 감수한다.
엇갈렸으면 `claude list` 의 ⚠ 안내대로 add/remove 로 바로잡는다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from devdash.claude.registry import DEFAULT_SESSIONS_PATH, editing, norm
from devdash.config import ROOT

PENDING = ROOT / "logs" / "clear-pending.json"
HOOK_LOG = ROOT / "logs" / "claude-hooks.log"
PENDING_TTL = 60
"""초. 옛 대화 끝 → 새 대화 시작은 한 프로세스 안에서 곧바로 이어진다."""


def log(msg: str, path: Path = HOOK_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")


def _read_pending(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_pending(pending: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")


def on_end(data: dict, pending_path: Path = PENDING, log_path: Path = HOOK_LOG) -> None:
    if data.get("reason") != "clear":
        return
    pending = _read_pending(pending_path)
    pending[norm(data.get("cwd", ""))] = {"old": data["session_id"], "at": time.time()}
    _write_pending(pending, pending_path)
    log(f"clear 시작  옛 {data['session_id'][:8]}  {data.get('cwd')}", log_path)


def on_start(
    data: dict,
    pending_path: Path = PENDING,
    sessions_path: Path = DEFAULT_SESSIONS_PATH,
    log_path: Path = HOOK_LOG,
) -> None:
    if data.get("source") != "clear" or not pending_path.exists():
        return
    pending = _read_pending(pending_path)
    p = pending.pop(norm(data.get("cwd", "")), None)
    _write_pending(pending, pending_path)
    if not p or time.time() - p["at"] > PENDING_TTL:
        return
    old, new = p["old"], data["session_id"]
    with editing(sessions_path) as entries:
        hit = next((e for e in entries if e["id"] == old), None)
        if hit:
            hit["id"] = new  # 다른 필드(project_key 등)는 그대로 따라간다
    if hit:
        log(f"갱신  {hit['name']}  {old[:8]} → {new[:8]}", log_path)
    else:
        log(f"목록에 없음 (그대로 둠)  {old[:8]} → {new[:8]}", log_path)
