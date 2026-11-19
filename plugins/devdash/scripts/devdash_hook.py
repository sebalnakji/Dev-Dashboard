"""devdash 훅 — 기록 1겹(자동)과 2겹(알림 · 강제). Claude Code 가 이벤트마다 부른다 (ADR 0007).

    UserPromptSubmit   세션 이름을 정하고, 필요할 때 "작업 기록 폴더 · 세션 이름" 알림
    SessionStart       요약(compact) · 이어 열기(resume) 뒤 알림 다시. /clear 면 이름 넘겨받기
    SessionEnd         /clear 직전 이름을 맡겨 둔다
    PreToolUse         파일을 고치기 직전 내용을 스냅샷으로 (그 차례에 그 파일을 처음 고칠 때만)
    PostToolUse        파일을 만들거나 고친 직후 변경 기록 한 줄 (진행 중 할 일 번호와 함께)과
                       고친 직후 내용의 스냅샷. 셸 명령(Bash)이 바꾼 파일도 Claude 가 남긴 바뀐
                       부분으로 같이 남긴다 (devdash_snap). 차례의 첫 변경이면 할 일에 적으라고
                       한 번 알린다 (2겹 알림)
    Stop               마지막 작업 저장. 이 세션의 완료 할 일에 '한 일'이 비면 한 번 막는다
                       (2겹 강제)

등록된 프로젝트가 아니면 아무것도 하지 않는다. 훅은 모든 세션에 끼어들므로 어떤 오류도
세션을 방해하지 않게, 언제나 종료 코드 0 으로 끝내고 오류는 ~/.devdash/hook-errors.log 에만 남긴다.
표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent

LAST_MESSAGE_LIMIT = 500
"""세션의 마지막 작업은 화면에 한두 줄로 보이므로 앞부분만 둔다."""

FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

RECORDS_HINT = "[devdash] 작업 기록 폴더: {tasks} · 세션 이름: {name}"
"""Claude 에게 알리는 줄. '작업 기록 폴더' · '세션 이름' 낱말은 SKILL.md 와 같아야 한다
(테스트로 묶음). 매 차례가 아니라 필요할 때만 낸다 — 매 차례면 100차례에 수천 토큰이
쌓인다 (3단계 Fable 검토)."""

BLOCK_REASON = (
    "[devdash] 이번 차례에 완료(done)로 바꾼 할 일의 '## 한 일'이 비어 있습니다.\n{items}\n"
    "무엇을 했는지 '- YYYY-MM-DD 무엇을 했나 — 왜' 로 한 줄 이상 적은 뒤 끝내세요. "
    "아직 끝나지 않았으면 status 를 doing 으로 되돌리고 '## 남은 일' 에 이어서 할 것을 적으세요."
)
"""끝내기를 막을 때 Claude 에게 주는 이유. 무엇을 하면 풀리는지까지 적는다."""

REMIND_DOING = (
    "[devdash] 이번 차례에 파일을 고쳤습니다. 의미 있는 진척이면 끝내기 전에 진행 중 할 일 "
    "{task} 의 '## 한 일' 맨 위에 한 줄 적으세요."
)
REMIND_NONE = (
    "[devdash] 이 세션에 진행 중(doing) 할 일이 없어, 이 변경은 어느 할 일에도 붙지 않습니다. "
    "작은 수정이면 그대로 두고, 아니면 기록 규칙대로 할 일을 잡으세요."
)
REMIND_MANY = (
    "[devdash] 이 세션의 진행 중(doing) 할 일이 여럿이라({tasks}) 변경을 어디에도 붙이지 못합니다. "
    "하나만 doing 으로 두세요."
)

MTIME_SLACK = 1.0
"""초. 파일 수정 시각의 정밀도 차이로 이번 차례에 바꾼 파일을 놓치지 않게 둔 여유."""

CLEAR_TTL = 60
"""초. /clear 의 끝(옛 번호)과 시작(새 번호)은 한 프로세스 안에서 곧바로 이어진다."""

ERROR_LOG_LIMIT = 512 * 1024


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _store():
    sys.path.insert(0, str(HERE))
    import devdash_store
    import devdash_tasks

    return devdash_store, devdash_tasks


def _snap():
    sys.path.insert(0, str(HERE))
    import devdash_snap

    return devdash_snap


# --- 세션 이름 ---


def resolve_name(store, tasks_mod, key: str, data: dict) -> tuple[str, bool]:
    """이 세션의 이름과, 알림을 다시 내야 하는지.

    사람이 붙인 이름(session_title)이 새로 오면 그것, 아니면 저장해 둔 이름, 그것도 없으면
    '<프로젝트>_001' 처럼 새로 붙인다. session_title 은 사람이 붙인 이름만 담는다 — 앱의
    자동 이름은 오지 않는다 (실제 세션 3차례로 확인).

    다른 세션이 이미 쓰는 이름이면 'develope_2' 처럼 가른다 (store.claim_name). 받은 이름이
    바뀌었는지는 가른 이름이 아니라 받은 이름(title)끼리 비교한다. 이름이 바뀌면 이 세션이
    잡고 있던 할 일의 session: 도 옮긴다.
    """
    sid = data["session_id"]
    info = store.session_info(key, sid)
    stored = info.get("name")
    title = data.get("session_title")
    if title and title != info.get("title"):
        name = store.claim_name(key, sid, title)
        store.update_session(key, sid, title=title)
        if stored and stored != name:
            move_task_owner(store, tasks_mod, key, stored, name)
    elif stored:
        name = stored
    else:
        name = store.next_auto_name(key)
        store.update_session(key, sid, name=name)
    return name, info.get("hinted") != name


def move_task_owner(store, tasks_mod, key: str, old: str, new: str) -> None:
    """이름이 바뀐 세션의 진행 중·완료 할 일에서 session: 을 새 이름으로 바꾼다.
    주인이 사라지지 않게 한다."""
    for path in (store.project_dir(key) / "tasks").glob("T-*.md"):
        meta = tasks_mod.read_meta(path)
        if meta.get("session") != old or meta.get("status") not in ("doing", "done"):
            continue
        # newline="" — 읽고 쓰며 LF 를 CRLF 로 바꾸지 않게
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        changed = tasks_mod.set_meta(text, "session", new)
        if changed != text:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(changed)


def hint(store, key: str, sid: str, name: str) -> None:
    tasks = store.project_dir(key) / "tasks"
    # 이 출력이 Claude 의 문맥에 들어간다. 저장보다 먼저 낸다 — 저장이 실패해도 알림은 가게
    print(RECORDS_HINT.format(tasks=tasks, name=name))
    with contextlib.suppress(Exception):  # 실패하면 다음 차례에 한 번 더 알릴 뿐이다
        store.update_session(key, sid, hinted=name)


def task_files(store, tasks_mod, key: str) -> list[tuple[int, Path]]:
    """이름이 형식(T-번호-제목.md)에 맞는 할 일 파일들, 번호 순 (T-2 가 T-10 앞).
    알림과 강제가 같은 파일을 보게 한 곳에서 거른다 (4단계 Fable 검토)."""
    out = []
    for path in (store.project_dir(key) / "tasks").glob("T-*.md"):
        m = tasks_mod.FILE_NAME.match(path.name)
        if m:
            out.append((int(m.group(1)), path))
    return sorted(out)


def doing_tasks(store, tasks_mod, key: str, name: str) -> list[str]:
    """이 이름의 세션이 진행 중인 할 일 번호들. 규칙상 하나여야 한다."""
    found = []
    for number, path in task_files(store, tasks_mod, key):
        meta = tasks_mod.read_meta(path)
        if meta.get("status") == "doing" and meta.get("session") == name:
            found.append(f"T-{number}")
    return found


def first_change_in_turn(store, key: str, sid: str, prompt_id: str) -> bool:
    """이 차례(prompt_id)의 첫 파일 변경이면 표시해 두고 True.

    한 응답의 Edit 여럿은 PostToolUse 가 동시에 돌아서(공식 문서) 확인과 표시를 잠근 채 한다 —
    잠그지 않으면 같은 알림이 두 번 갔다 (4단계 Fable 재현: 10번 중 4번).
    """
    with store.locked(store.project_dir(key) / "remind"):
        if store.session_info(key, sid).get("reminded") == prompt_id:
            return False
        store.update_session(key, sid, reminded=prompt_id)
    return True


def remind(doing: list[str]) -> None:
    """차례의 첫 파일 변경 때 한 번, 할 일에 적으라고 알린다 (2겹 알림).

    Stop 훅은 끝내기를 막지 않고는 Claude 에게 말을 전할 수 없어서(공식 문서), 끝난 뒤
    확인하는 대신 아직 일하는 중에 미리 알린다. 막지 않는다.
    """
    if len(doing) == 1:
        text = REMIND_DOING.format(task=doing[0])
    elif doing:
        text = REMIND_MANY.format(tasks=", ".join(doing))
    else:
        text = REMIND_NONE
    # 도구 결과 옆에 붙어 Claude 가 바로 본다
    out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
    print(json.dumps(out, ensure_ascii=False))


# --- 이벤트 ---


def on_user_prompt(store, tasks_mod, key: str, data: dict) -> None:
    sid = data.get("session_id")
    if not sid:
        return
    name, need_hint = resolve_name(store, tasks_mod, key, data)
    if need_hint:
        hint(store, key, sid, name)
    # 강제(Stop)가 '이번 차례에 완료로 바꾼 할 일'만 보게 차례가 시작된 시각을 둔다
    store.update_session(key, sid, turn_started=time.time())


def on_session_start(store, key: str, data: dict) -> None:
    sid = data.get("session_id")
    source = data.get("source")
    if not sid:
        return
    if source == "clear":
        take_name_after_clear(store, key, data)
    if source in ("clear", "compact", "resume"):
        name = store.session_info(key, sid).get("name")
        if name:  # 이름이 아직 없으면 첫 UserPromptSubmit 이 정하고 알린다
            hint(store, key, sid, name)


def on_session_end(store, key: str, data: dict) -> None:
    """/clear 직전: 옛 번호를 작업 폴더 기준으로 맡겨 둔다.
    새 번호가 시작될 때 이름을 넘겨받는다."""
    if data.get("reason") != "clear" or not data.get("session_id"):
        return
    pending = store.project_dir(key) / "clear-pending.json"
    with store.locked(pending):
        items = _read_json(pending)
        items[store.same(data.get("cwd", ""))] = {"old": data["session_id"], "at": time.time()}
        pending.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def take_name_after_clear(store, key: str, data: dict) -> None:
    pending = store.project_dir(key) / "clear-pending.json"
    with store.locked(pending):
        items = _read_json(pending)
        p = items.pop(store.same(data.get("cwd", "")), None)
        pending.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    if not p or time.time() - p["at"] > CLEAR_TTL:
        return
    old = store.session_info(key, p["old"])
    if old.get("name"):
        # 같은 이름을 이어 쓰므로 그 이름으로 적힌 진행 중 할 일도 그대로 이 세션 것이 된다.
        # 이름을 가르지 않는 유일한 경우다 — 옛 번호는 끝났고 이 세션이 그 대화를 잇는다.
        # 받은 이름(title)도 넘겨야 다음 차례에 같은 이름이 와도 '새 이름'으로 보지 않는다
        # 마지막 작업도 넘긴다 — 새 번호의 첫 Stop 전까지 화면의 세션 칸이 비지 않게
        store.update_session(
            key,
            data["session_id"],
            name=old["name"],
            title=old.get("title"),
            last_message=old.get("last_message"),
            last_at=old.get("last_at"),
        )


def _target(store, data: dict, root: str) -> tuple[str, str, str | None] | None:
    """파일 도구가 고치는 파일 (실제 경로, 저장소 기준 경로, 작업용 복사본).
    프로젝트 밖이면 None."""
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        return None
    where = store.relative_in_project(path, root)
    if where is None:  # 프로젝트 밖 파일 (메모리 · 할 일 파일 · 임시 파일)
        return None
    return path, where[0], where[1]


def _turn(store, tasks_mod, key: str, data: dict) -> tuple[str | None, list[str]]:
    """(세션 이름, 그 세션의 진행 중 할 일들). 이름이 없으면 기록 규칙을 모르는 세션이다."""
    sid = data.get("session_id")
    name = store.session_info(key, sid).get("name") if sid else None
    return name, (doing_tasks(store, tasks_mod, key, name) if name else [])


def _first_in_turn(store, key: str, sid: str | None, prompt: str | None, spot: str) -> bool:
    """이 차례에 이 파일의 직전 내용을 아직 안 남겼나 — 남겼다고 적어 둔다.
    차례마다 처음 고치기 직전만 남긴다 (작업 비교는 그 작업의 첫 차례 직전을 쓴다)."""
    if not sid:
        return True
    snap = store.session_info(key, sid).get("snap") or {}
    seen = snap.get("paths", []) if snap.get("prompt") == prompt else []
    if spot in seen:
        return False
    store.update_session(key, sid, snap={"prompt": prompt, "paths": [*seen, spot]})
    return True


def _snapshot(store, snap, key: str, data: dict, task, rel: str, worktree, when: str, blob, tool):
    path = snap.log_path(store.project_dir(key))
    record = {
        "at": now(),
        "session": data.get("session_id"),
        "prompt": data.get("prompt_id"),
        "task": task,
        "path": rel,
        "worktree": worktree,
        "when": when,
        "blob": blob,
        "tool": tool,
    }
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8", "replace")
    with store.locked(path), open(path, "ab") as f:
        f.write(line)


def on_pre_tool(store, tasks_mod, key: str, root: str, data: dict) -> None:
    """고치기 직전: 그 차례에 그 파일을 처음 고칠 때만 지금 내용을 넣는다.
    없던 파일이면 blob None."""
    target = _target(store, data, root)
    if target is None:
        return
    full, rel, worktree = target
    if not _first_in_turn(
        store, key, data.get("session_id"), data.get("prompt_id"), f"{worktree or ''}:{rel}"
    ):
        return
    snap = _snap()
    content = snap.read_file(full)
    blob = snap.put(store.project_dir(key), content) if content is not None else None
    _, doing = _turn(store, tasks_mod, key, data)
    task = doing[0] if len(doing) == 1 else None
    _snapshot(store, snap, key, data, task, rel, worktree, "before", blob, data.get("tool_name"))


def on_post_tool(store, tasks_mod, key: str, root: str, data: dict) -> None:
    if data.get("tool_name") == "Bash":
        on_post_bash(store, tasks_mod, key, root, data)
        return
    target = _target(store, data, root)
    if target is None:
        return
    full, rel, worktree = target
    name, doing = _turn(store, tasks_mod, key, data)
    task = doing[0] if len(doing) == 1 else None
    response = data.get("tool_response") or {}
    op = "create" if isinstance(response, dict) and response.get("type") == "create" else "modify"
    _record(store, key, data, task, op, rel, worktree, None)
    with _snapshot_errors():
        snap = _snap()
        content = snap.read_file(full)
        blob = snap.put(store.project_dir(key), content) if content is not None else None
        tool = data.get("tool_name")
        _snapshot(store, snap, key, data, task, rel, worktree, "after", blob, tool)
    _remind_once(store, key, data, name, doing)


SHELL_FILES_LIMIT = 100
"""셸 명령 하나가 바꾼 파일이 이보다 많으면 앞의 것만 남긴다 (만들어 낸 파일 무더기 등)."""

SHELL_BLOB_LIMIT = 5 * 1024 * 1024
"""셸 명령으로 바뀐 파일이 이보다 크면 내용은 남기지 않는다 (unknown)."""

PENDING_TTL = 3600.0
"""명령 직전 상태를 이만큼 지나도 직후가 안 오면 버린다 (끊긴 명령)."""


def _shell_state(store, key: str, sid: str | None) -> Path:
    """세션별 셸 명령 상태: 직전 git 상태(pending)와 고쳐져 있던 파일의 내용 번호(cache)."""
    return store.project_dir(key) / "shell" / f"{sid or 'none'}.json"


def on_pre_bash(store, key: str, root: str, data: dict) -> None:
    """셸 명령 직전: git status 와, 이미 고쳐져 있던 파일의 지금 내용을 떠 둔다.
    떠 둔 내용은 (시각 · 크기)가 그대로면 다시 넣지 않는다. git 이 아닌 폴더는 하지 않는다."""
    snap = _snap()
    top = snap.find_top(data.get("cwd") or root)
    if top is None:
        return
    files = snap.status(top)
    path = _shell_state(store, key, data.get("session_id"))
    path.parent.mkdir(parents=True, exist_ok=True)
    project_dir = store.project_dir(key)
    with store.locked(path):
        state = _read_json(path)
        old_cache = state.get("cache") or {}
        cache, before, need = {}, {}, []
        for rel, code in files.items():
            full = str(top / rel)
            st = snap.file_stat(full)
            entry = {"code": code, "stat": st, "blob": None}
            if st is not None:
                hit = old_cache.get(full)
                if st[1] > SHELL_BLOB_LIMIT:
                    entry["blob"] = "unknown"
                elif hit and hit[0] == st:
                    entry["blob"] = hit[1]
                else:
                    need.append((full, entry))
            before[rel] = entry
        blobs = snap.put_paths(project_dir, [f for f, _ in need])
        for (_full, entry), blob in zip(need, blobs, strict=True):
            entry["blob"] = blob
        for rel, entry in before.items():
            if entry["stat"] is not None and entry["blob"] not in (None, "unknown"):
                cache[str(top / rel)] = [entry["stat"], entry["blob"]]
        pending = {
            k: v
            for k, v in (state.get("pending") or {}).items()
            if time.time() - v.get("at", 0) < PENDING_TTL
        }
        pending[data.get("tool_use_id") or "last"] = {
            "top": str(top),
            "files": before,
            "at": time.time(),
        }
        path.write_text(json.dumps({"cache": cache, "pending": pending}), encoding="utf-8")


def on_post_bash(store, tasks_mod, key: str, root: str, data: dict) -> None:
    """셸 명령이 바꾼 파일. git 저장소면 직전에 떠 둔 git 상태와 지금을 견준다.
    git 이 아닌 폴더는 Claude 가 남긴 bashEditDiff 가 있을 때만."""
    snap = _snap()
    if snap.find_top(data.get("cwd") or root) is None:
        _post_bash_from_diff(store, tasks_mod, key, root, data)
        return
    path = _shell_state(store, key, data.get("session_id"))
    if not path.exists():
        return
    with store.locked(path):
        state = _read_json(path)
        pending = state.get("pending") or {}
        pre = pending.pop(data.get("tool_use_id") or "last", None)
        if pre is None:
            return
        path.write_text(json.dumps({**state, "pending": pending}), encoding="utf-8")
    top = Path(pre["top"])
    was = pre["files"]
    changed = []
    for rel in dict.fromkeys([*snap.status(top), *was]):
        full = str(top / rel)
        st = snap.file_stat(full)
        if rel in was:
            if was[rel]["stat"] == st:
                continue  # 명령 전에도 고쳐져 있었고 그대로다
            before = was[rel]["blob"]  # 없던 파일이면 None
        else:
            before = "HEAD"  # 명령 전에는 깨끗했다 — 직전 내용은 마지막 커밋
        changed.append((rel, full, st, before))
    if not changed:
        return
    name, doing = _turn(store, tasks_mod, key, data)
    task = doing[0] if len(doing) == 1 else None
    project_dir = store.project_dir(key)
    recorded = False
    for rel, full, st, before in changed[:SHELL_FILES_LIMIT]:
        where = store.relative_in_project(full, root)
        if where is None:
            continue
        rel_p, worktree = where
        try:
            if before == "HEAD":
                head = snap.head_content(top, rel)
                before = snap.put(project_dir, head) if head is not None else None
            if st is None:
                after = None
            elif st[1] > SHELL_BLOB_LIMIT:
                after = "unknown"
            else:
                content = snap.read_file(full)
                after = snap.put(project_dir, content) if content is not None else None
        except Exception:
            with contextlib.suppress(Exception):
                log_error(["devdash_hook.py", "snapshot"])
            before, after = "unknown", "unknown"
        if before == after and before not in ("unknown",):
            continue  # 건드렸지만 내용은 그대로
        op = "create" if before is None else "delete" if after is None else "modify"
        _record(store, key, data, task, op, rel_p, worktree, "Bash")
        recorded = True
        with _snapshot_errors():
            spot = f"{worktree or ''}:{rel_p}"
            if _first_in_turn(store, key, data.get("session_id"), data.get("prompt_id"), spot):
                _snapshot(store, snap, key, data, task, rel_p, worktree, "before", before, "Bash")
            _snapshot(store, snap, key, data, task, rel_p, worktree, "after", after, "Bash")
    if recorded:
        _remind_once(store, key, data, name, doing)


def _post_bash_from_diff(store, tasks_mod, key: str, root: str, data: dict) -> None:
    """git 이 아닌 폴더: Claude 가 결과에 남긴 bashEditDiff 로 바뀐 파일을 안다. 직전 내용은
    그 차례에 처음 바뀐 파일이면 지금 내용에 바뀐 부분을 거꾸로 적용해 만든다.
    맞지 않으면 unknown. bashEditDiff 는 내부 형식이라 늘 오지는 않는다 — 안 오면 못 잡는다."""
    snap = _snap()
    name, doing = _turn(store, tasks_mod, key, data)
    task = doing[0] if len(doing) == 1 else None
    project_dir = store.project_dir(key)
    changed = False
    for f in snap.bash_files(data.get("tool_response")):
        where = store.relative_in_project(f["filePath"], root)
        if where is None:
            continue
        rel, worktree = where
        content = snap.read_file(f["filePath"])
        if content is None:
            continue  # 지워진 파일 — 비교할 이후가 없다
        hunks = f.get("hunks") if isinstance(f.get("hunks"), list) else []
        created = bool(hunks) and all(int(h.get("oldLines", 1)) == 0 for h in hunks)
        _record(store, key, data, task, "create" if created else "modify", rel, worktree, "Bash")
        changed = True
        with _snapshot_errors():
            spot = f"{worktree or ''}:{rel}"
            if _first_in_turn(store, key, data.get("session_id"), data.get("prompt_id"), spot):
                before = _before_from_hunks(snap, project_dir, content, hunks, created)
                _snapshot(store, snap, key, data, task, rel, worktree, "before", before, "Bash")
            after = snap.put(project_dir, content)
            _snapshot(store, snap, key, data, task, rel, worktree, "after", after, "Bash")
    if changed:
        _remind_once(store, key, data, name, doing)


@contextlib.contextmanager
def _snapshot_errors():
    """스냅샷이 실패해도(git 이 없음 · 디스크 등) 변경 기록과 알림은 해야 한다.
    오류만 남기고 넘어간다."""
    try:
        yield
    except Exception:
        with contextlib.suppress(Exception):
            log_error(["devdash_hook.py", "snapshot"])


def _before_from_hunks(snap, project_dir, content: bytes, hunks: list, created: bool):
    """바뀐 부분을 거꾸로 적용한 직전 내용의 blob. 새로 만든 파일이면 None, 못 만들면 'unknown'."""
    if created:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"
    before = snap.reverse_patch(text, hunks)
    if before is None and "\r\n" in text:
        # 바뀐 부분의 줄에는 줄 끝 \r 이 없을 수 있다 — 줄바꿈을 맞춰 한 번 더
        before = snap.reverse_patch(text.replace("\r\n", "\n"), hunks)
        before = before.replace("\n", "\r\n") if before is not None else None
    if before is None:
        return "unknown"
    return snap.put(project_dir, before.encode("utf-8"))


def _record(store, key: str, data: dict, task, op: str, rel: str, worktree, tool) -> None:
    record = {
        "at": now(),
        "session": data.get("session_id"),
        "prompt": data.get("prompt_id"),
        "agent": data.get("agent_id"),  # 서브에이전트가 고쳤으면 그 번호
        # 그 세션이 이때 진행 중이던 할 일 — 한 세션이 T-1 을 끝내고 T-2 를 해도 나눌 수 있게.
        # 없거나 여럿이면 어디에 붙일지 모르므로 비운다
        "task": task,
        "op": op,
        "path": rel,
        "worktree": worktree,
    }
    if tool:
        record["tool"] = (
            tool  # 셸 명령으로 바뀐 파일 (파일 도구는 적지 않는다 — 옛 기록과 같은 모양)
        )
    store.append_activity(key, record)


def _remind_once(store, key: str, data: dict, name, doing: list[str]) -> None:
    # 서브에이전트는 할 일을 쓰지 않으므로(기록 규칙) 알리지 않는다. 이름이 없으면 기록 규칙을
    # 모르는 세션이다 (알림을 받기 전)
    sid, prompt_id = data.get("session_id"), data.get("prompt_id")
    if (
        name
        and prompt_id
        and not data.get("agent_id")
        and first_change_in_turn(store, key, sid, prompt_id)
    ):
        remind(doing)


def on_stop(store, tasks_mod, key: str, data: dict) -> None:
    sid = data.get("session_id")
    if not sid:
        return
    message = (data.get("last_assistant_message") or "").strip()
    if message:
        # 시각은 화면 세션 칸의 "언제" — 파일 수정 시각은 차례 시작 때도 바뀌어 쓸 수 없다
        store.update_session(key, sid, last_message=message[:LAST_MESSAGE_LIMIT], last_at=now())
    # 2겹 강제. 한 번 막은 뒤(stop_hook_active)에는 다시 막지 않는다 — 채우지 못하는 상황에서
    # 세션이 끝없이 붙잡히지 않게. 막는 것은 확실한 것 하나뿐이다 (ADR 0007)
    if data.get("stop_hook_active"):
        return
    info = store.session_info(key, sid)
    name, since = info.get("name"), info.get("turn_started")
    if not name or since is None:
        return
    empty = done_without_work(store, tasks_mod, key, name, since)
    if empty:
        # decision:block 도 막지만 화면에 'Stop hook error' 로 뜬다. additionalContext 는
        # 같은 효과에 'Stop hook feedback' 으로 뜬다 (공식 문서 · 실제 백그라운드 세션으로 확인)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "Stop",
                        "additionalContext": BLOCK_REASON.format(
                            items="\n".join(f"- {path}: {'; '.join(why)}" for path, why in empty)
                        ),
                    }
                },
                ensure_ascii=False,
            )
        )


def done_without_work(
    store, tasks_mod, key: str, name: str, since: float
) -> list[tuple[Path, list[str]]]:
    """이번 차례(since 이후)에 바뀐, 이 세션 이름의 완료 할 일 중 '한 일'이 비었거나 없는 것.
    파일마다 형식 검사가 찾은 이유를 함께 준다 — '## 한일' 처럼 제목이 틀렸거나 두 번 있어서
    비어 보이는 경우에 Claude 가 무엇을 고칠지 알게.

    이번 차례로 좁히는 까닭: 사람이 손으로 완료한 옛 할 일 하나가 매 차례 끝내기를 막았고,
    Claude 는 모르는 일을 지어 적어야 했다 (4단계 Fable 재현). ADR 0007 도 '완료로 바꿨는데' 다.
    """
    out = []
    for _, path in task_files(store, tasks_mod, key):
        try:
            if path.stat().st_mtime < since - MTIME_SLACK:
                continue
            data = tasks_mod.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        meta = data["meta"]
        if meta.get("status") != "done" or meta.get("session") != name:
            continue
        if any(
            not tasks_mod.is_filled(data["sections"].get(s)) for s in tasks_mod.REQUIRED_WHEN_DONE
        ):
            out.append((path, tasks_mod.problems(path)))
    return out


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run(event: str, data: dict) -> None:
    # 파일을 바꾸지 않는 도구는 프로젝트를 찾기(git 호출) 전에 끝낸다. 모든 세션에서 도는 훅이다
    tool = data.get("tool_name")
    if event in ("PreToolUse", "PostToolUse"):
        if tool == "Bash":
            tool_input = data.get("tool_input")
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            if _snap().read_only(command):  # ls · cat · git log … : git status 도 돌리지 않는다
                return
        elif tool not in FILE_TOOLS:
            return
    store, tasks_mod = _store()
    found = store.find_project(os.environ.get("CLAUDE_PROJECT_DIR"), data.get("cwd"))
    if found is None:
        return
    key, root = found
    if event == "UserPromptSubmit":
        on_user_prompt(store, tasks_mod, key, data)
    elif event == "SessionStart":
        on_session_start(store, key, data)
    elif event == "SessionEnd":
        on_session_end(store, key, data)
    elif event == "PreToolUse" and tool == "Bash":
        on_pre_bash(store, key, root, data)
    elif event == "PreToolUse":
        on_pre_tool(store, tasks_mod, key, root, data)
    elif event == "PostToolUse":
        on_post_tool(store, tasks_mod, key, root, data)
    elif event == "Stop":
        on_stop(store, tasks_mod, key, data)


def log_error(argv: list[str]) -> None:
    home = Path(os.environ.get("DEVDASH_HOME") or Path.home() / ".devdash")
    home.mkdir(parents=True, exist_ok=True)
    log = home / "hook-errors.log"
    if log.exists() and log.stat().st_size > ERROR_LOG_LIMIT:
        os.replace(log, log.with_suffix(".log.1"))  # 끝없이 커지지 않게 한 번 밀어 둔다
    with open(log, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"{now()} {argv[1:]}\n{traceback.format_exc()}\n")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        event = argv[1] if len(argv) > 1 else ""
        raw = sys.stdin.buffer.read().decode("utf-8-sig")
        data = json.loads(raw) if raw.strip() else {}
        if isinstance(data, dict):
            run(event, data)
    except Exception:
        with contextlib.suppress(Exception):
            log_error(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
