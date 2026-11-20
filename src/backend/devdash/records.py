"""프로젝트 탭이 읽는 작업 기록.

기록의 위치(~/.devdash)와 할 일 파일 형식의 정의는 플러그인 한 곳(plugins/devdash/scripts)에 있다.
훅과 대시보드가 같은 정의를 쓰도록 복사하지 않고 그대로 불러 쓴다.

화면의 칸마다 함수 하나를 둔다 (화면이 그리는 것에 API 를 맞춘다).
화면은 몇 초마다 다시 묻으므로(폴링), 매번 무거운 일을 하지 않게 신경 쓴다.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from datetime import datetime

from devdash import commit_summary as commit_summary_ai
from devdash import gitdiff
from devdash.claude import chat, registry
from devdash.home import PLUGIN_SCRIPTS  # noqa: F401 — 플러그인 스크립트를 import 경로에 올린다
from devdash.local import run_bounded

# isort: split
# 위의 devdash.home 이 플러그인 스크립트 폴더를 import 경로에 올린 뒤에야 불러올 수 있다
import contextlib

import devdash_snap as snap
import devdash_store as store
import devdash_tasks as tasks

COMMITS = 50
"""오른쪽 칸에 보이는 최근 커밋 수."""

WALK_LIMIT = 5000
"""git 이 아닌 폴더를 훑을 때 파일 수 상한. 넘으면 잘렸다고 알린다."""

WALK_SKIP = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".idea",
    "dist",
    "build",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
}
"""폴더를 훑을 때 들어가지 않는 의존성 · 빌드 · 캐시 폴더. 파일이 수만 개라 트리가 묻힌다.
git 이 아닌 폴더와, git 저장소의 git 제외 폴더를 훑을 때 쓴다.
이름 끝이 .egg-info 인 것도 뺀다 (_skip_dir)."""


def _skip_dir(name: str) -> bool:
    return name in WALK_SKIP or name.endswith(".egg-info")


def _worktree(path: str) -> bool:
    """git 작업용 복사본(worktree)인가. 그 `.git` 은 폴더가 아니라 본 저장소를 가리키는 파일이다.

    안쪽의 독립 저장소(`.git` 이 폴더 — 예: 공개 저장소 안에 따로 둔 비공개 문서 저장소)는
    이 프로젝트의 파일이라 보여 준다. 작업용 복사본은 같은 파일이 두 번 보이므로 뺀다."""
    return os.path.isfile(os.path.join(path, ".git"))


def _ignored_files(root: str) -> tuple[list[str], bool]:
    """git 제외 파일들 (.env · CLAUDE.local.md · 로컬 DB …). 상한을 넘으면 (앞부분, True).

    [파일] 보기는 git 이 아니라 이 PC 의 작업 폴더를 보므로 git 에 올리지 않는 파일도 보인다
    (사용자 결정). 다만 의존성 · 빌드 · 캐시 폴더(_skip_dir)와 작업용 복사본(_worktree)은
    들어가지 않는다. 안쪽의 독립 저장소는 들어간다 (그 `.git` 폴더는 _skip_dir 이 뺀다).
    git 이 알려 주는 제외 폴더 안만 훑으므로 저장소 전체를 다시 훑지 않는다."""
    listed = _git(
        root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"
    )
    out: set[str] = set()
    for entry in (listed or "").split("\0"):
        if not entry:
            continue
        parts = entry.rstrip("/").split("/")
        if any(_skip_dir(p) for p in parts):
            continue
        if not entry.endswith("/"):
            out.add(entry)
        elif not _worktree(os.path.join(root, entry)):
            for dirpath, dirnames, filenames in os.walk(os.path.join(root, entry)):
                dirnames[:] = [
                    d
                    for d in dirnames
                    if not _skip_dir(d) and not _worktree(os.path.join(dirpath, d))
                ]
                rel = os.path.relpath(dirpath, root).replace("\\", "/")
                out.update(f"{rel}/{f}" for f in filenames)
                if len(out) >= WALK_LIMIT:
                    return sorted(out)[:WALK_LIMIT], True
        if len(out) >= WALK_LIMIT:
            return sorted(out)[:WALK_LIMIT], True
    return sorted(out), False


def _git(root: str, *args: str) -> str | None:
    """git 명령의 출력 (UTF-8). git 이 아니거나 실패하면 None.

    --no-optional-locks: 폴링하는 status 가 index 를 다시 쓰면, 같은 저장소에서 커밋하는
    세션이 index.lock 에 부딪힐 수 있다.

    바이트로 받아 직접 푼다. 글 모드는 줄 안의 CR 하나도 줄바꿈으로 바꿔, diff 의 한 줄이 두 줄로
    쪼개지고 변경 덩어리가 중간에서 잘렸다.
    """
    try:
        r = subprocess.run(
            ["git", "--no-optional-locks", "-C", root, *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None


# --- 변경 기록 (폴링마다 다시 읽지 않게) ---

_activity_cache: dict[str, tuple[tuple[int, int], list[dict]]] = {}
_activity_lock = threading.Lock()


def _activity(key: str) -> list[dict]:
    """변경 기록 전체. 파일 크기 · 수정 시각이 그대로면 앞서 읽은 것을 쓴다 — 기록이
    수만 줄이면 한 번 읽는 데 0.5초가 걸린다."""
    path = store.project_dir(key) / "activity.jsonl"
    try:
        st = path.stat()
    except OSError:
        return []
    stamp = (st.st_size, st.st_mtime_ns)
    with _activity_lock:
        hit = _activity_cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    records = store.read_activity(key)
    with _activity_lock:
        _activity_cache[key] = (stamp, records)
    return records


# --- 프로젝트 목록 · 추가 ---


def project(key: str) -> dict | None:
    """등록된 프로젝트 {"root", "name"}. 없거나 깨진 등록(root 없음)이면 None."""
    info = store.projects().get(key)
    return info if isinstance(info, dict) and info.get("root") else None


def list_projects() -> list[dict]:
    """프로젝트 목록. 사람이 끌어서 정한 순서(set_order)대로, 순서에 없는 것은 그 뒤에 이름순.
    진행 중 할 일 개수는 탭에서 프로젝트 이름 옆에 보인다.
    개수에는 상태만 필요해 머리말만 읽는다 (폴링이 싸도록)."""
    out = []
    for key in store.projects():
        info = project(key)
        if info is None:
            continue
        doing = 0
        for path in (store.project_dir(key) / "tasks").glob("T-*.md"):
            status = (
                tasks.read_meta(path).get("status") if tasks.FILE_NAME.match(path.name) else None
            )
            doing += status == "doing"
        out.append(
            {"key": key, "name": info.get("name") or key, "root": info["root"], "doing": doing}
        )
    rank = {k: i for i, k in enumerate(_read_order())}
    return sorted(out, key=lambda p: (rank.get(p["key"], len(rank)), p["name"].casefold()))


# --- 순서 · 이름 바꾸기 · 등록 해제 (프로젝트 이름 상자의 끌기와 ⋮ 메뉴) ---


def _order_path():
    """프로젝트 순서. 브라우저가 아니라 기록 폴더에 둔다 — 어느 브라우저로 열어도 같게."""
    return store.home() / "project-order.json"


def _read_order() -> list[str]:
    try:
        value = json.loads(_order_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [k for k in value if isinstance(k, str)] if isinstance(value, list) else []


def _write_json(path, value) -> None:
    """통째로 바꿔 끼운다 (훅이 같은 파일을 읽는 중에 반쯤 쓴 내용을 보지 않게).

    임시 파일 이름은 무작위로 — 한 서버의 두 요청이 같은 임시 파일을 쓰지 않게. 짧게 둔다
    (윈도 경로 길이 260자에 걸리지 않게).
    윈도는 다른 곳에서 읽고 있는 파일을 바꿔 끼우지 못하므로(PermissionError)
    잠깐씩 몇 번 다시 해 본다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for wait in (0.05, 0.1, 0.2, 0.4, None):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if wait is None:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(wait)


def set_order(keys: list[str]) -> None:
    """화면에서 끌어 정한 순서를 저장한다. 등록되지 않은 키는 버린다."""
    known = set(store.projects())
    _write_json(_order_path(), [k for k in dict.fromkeys(keys) if k in known])


def rename(key: str, name: str) -> dict:
    """표시 이름만 바꾼다 (폴더 · 키 · 기록은 그대로). 빈 이름이면 ValueError.
    이미 붙은 세션 이름(예: '옛이름_001')은 바뀌지 않는다."""
    name = (name or "").strip()
    if not name:
        raise ValueError("이름을 적어 주세요")
    path = store.project_dir(key) / "project.json"
    info = json.loads(path.read_text(encoding="utf-8"))
    info["name"] = name
    _write_json(path, info)
    return next(p for p in list_projects() if p["key"] == key)


def _long(path: os.PathLike[str]) -> str:
    """윈도우에서 260자 넘는 경로도 다룰 수 있게 (스냅샷 저장소 안쪽은 경로가 깊다)."""
    full = os.path.abspath(path)
    return "\\\\?\\" + full if os.name == "nt" and not full.startswith("\\\\") else full


def snapshot_size(key: str) -> int:
    """이 프로젝트의 스냅샷(snapshots.git · snapshots.jsonl)이 차지하는 바이트."""
    d = store.project_dir(key)
    total = 0
    log = snap.log_path(d)
    if log.is_file():
        total += log.stat().st_size
    for folder, _dirs, names in os.walk(_long(snap.repo(d))):
        for name in names:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(folder, name))
    return total


def drop_snapshots(key: str) -> None:
    """스냅샷을 지운다 (되살릴 수 없다). git 이 만든 읽기 전용 파일도 지운다."""

    def writable_then_retry(func, path, _exc) -> None:
        os.chmod(path, stat.S_IWRITE)
        func(path)

    d = store.project_dir(key)
    if snap.repo(d).exists():
        shutil.rmtree(_long(snap.repo(d)), onerror=writable_then_retry)
    snap.log_path(d).unlink(missing_ok=True)


def unregister(key: str, snapshots: bool = False) -> None:
    """등록을 푼다. 기록(할 일 · 변경 기록 · 세션)은 지우지 않는다.

    project.json 을 project.removed.json 으로 비켜 두면 훅이 이 프로젝트를 못 찾아 기록을 멈추고,
    화면 목록에서도 빠진다. 같은 폴더를 다시 추가하면 남은 기록이 그대로 이어진다 —
    지운 기록은 되살릴 수 없어 지우지 않는 쪽을 골랐다.
    스냅샷(고치기 직전 · 직후 내용)은 용량이 클 수 있어 사용자가 고르면 함께 지운다.
    """
    d = store.project_dir(key)
    os.replace(d / "project.json", d / "project.removed.json")
    if snapshots:
        drop_snapshots(key)
    order = _read_order()
    if key in order:
        _write_json(_order_path(), [k for k in order if k != key])


def drop_members(key: str, data: dict) -> None:
    """등록을 푼 프로젝트로 정해 둔 세션을 자동 소속으로 돌린다
    (보이지 않는 프로젝트에 묶이지 않게)."""
    for sid, entry in data["sessions"].items():
        if entry.get("project") == key:
            registry.set_project(data, sid, None)


class _MainPaths:
    """한 요청 안에서 폴더별 원래 저장소를 한 번만 찾는다 (폴더마다 git 이 뜬다)."""

    def __init__(self) -> None:
        self._seen: dict[str, str | None] = {}

    def __call__(self, folder: str) -> str | None:
        """폴더의 원래 저장소 (원래 표기). 폴더가 없으면 None."""
        if folder not in self._seen:
            ok = bool(folder) and os.path.isdir(folder)
            self._seen[folder] = store.main_path(folder) if ok else None
        return self._seen[folder]


def candidates(rows: list[dict]) -> list[dict]:
    """추가 후보: 세션들의 작업 폴더 중 아직 등록 안 된 저장소.
    작업용 복사본 · 하위 폴더는 원래 저장소로 모은다. 폴더마다 거기서 일하는 세션 이름을 준다."""
    registered = {store.same(v["root"]) for k in store.projects() if (v := project(k))}
    main = _MainPaths()
    found: dict[str, dict] = {}
    for e in rows:
        folder = e.get("cwd") or ""
        root = main(folder)
        if root is None or store.same(root) in registered:
            continue
        c = found.setdefault(
            store.same(root), {"root": root, "name": root.rsplit("/", 1)[-1], "sessions": []}
        )
        c["sessions"].append(e.get("name", ""))
    return sorted(found.values(), key=lambda c: c["name"].casefold())


def add_project(folder: str, name: str | None = None) -> dict:
    """프로젝트를 등록하고 목록의 한 줄을 돌려준다.
    폴더가 없거나 · 상대 경로이거나 · 이미 등록됐거나 · 키가 겹치면 ValueError."""
    # 탐색기 '경로로 복사'는 따옴표를 붙인다
    folder = (folder or "").strip().strip('"').strip()
    if not os.path.isabs(folder):
        raise ValueError(f"전체 경로를 적어 주세요 (예: C:\\Projects\\이름): {folder}")
    if not os.path.isdir(folder):
        raise ValueError(f"폴더가 없습니다: {folder}")
    root = store.main_path(folder)
    existing = project(store.project_key(root))
    if existing:
        raise ValueError(f"이미 등록된 프로젝트입니다: {existing.get('name')} ({root})")
    key = store.register(root, name=(name or "").strip() or None)
    return next(p for p in list_projects() if p["key"] == key)


# --- 가운데: 할 일 · 정리 문서 ---


def _parse(path) -> dict | None:
    """할 일 파일 하나를 읽는다. 목록과 정리 문서가 같은 방식으로 읽게 한 곳에 둔다.
    UTF-8 이 아니거나 읽을 수 없으면 None (형식 문제로 따로 알린다)."""
    try:
        return tasks.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def task_list(key: str) -> list[dict]:
    """가운데 아래 칸. 번호순. 같은 번호가 둘이면 둘 다 보이므로 화면은 file 로 가른다.
    형식 문제는 폴더 단위 검사(같은 번호 · 한 세션의 진행 중 여럿)까지 담는다."""
    folder = store.project_dir(key) / "tasks"
    report = tasks.check_dir(folder) if folder.is_dir() else {}
    rows = []
    for path in folder.glob("T-*.md"):
        m = tasks.FILE_NAME.match(path.name)
        if not m or not path.is_file():
            continue
        data = _parse(path)
        meta = data["meta"] if data else {}
        rows.append(
            {
                "number": int(m.group(1)),
                "file": path.name,
                "title": (data and tasks.title_of(data, m.group(1))) or path.stem,
                "status": meta.get("status", ""),
                "session": meta.get("session", ""),
                "problems": report.get(path.name, []),
            }
        )
    return sorted(rows, key=lambda r: (r["number"], r["file"]))


def task_detail(key: str, number: int, file: str | None = None) -> dict | None:
    """정리 문서 칸: 목표 · 한 일 · 남은 일과, 이 작업에서 바뀐 파일(자동 변경 기록).
    같은 번호가 둘이면 화면이 고른 파일(file)을, 없으면 파일 이름순 첫째를 준다.
    바뀐 파일은 번호로 이어지므로 같은 번호 둘은 같은 목록을 보인다 (목록이 형식 문제로 알린다)."""
    folder = store.project_dir(key) / "tasks"
    found = sorted(
        p
        for p in folder.glob(f"T-{number}-*.md")
        if tasks.FILE_NAME.match(p.name) and p.is_file() and (file is None or p.name == file)
    )
    data = _parse(found[0]) if found else None
    if data is None:
        return None
    path, meta = found[0], data["meta"]
    changed: dict[str, dict] = {}
    for rec in _activity(key):
        if rec.get("task") != f"T-{number}" or not rec.get("path"):
            continue
        c = changed.setdefault(rec["path"], {"path": rec["path"], "created": False, "changes": 0})
        c["changes"] += 1
        c["created"] = c["created"] or rec.get("op") == "create"
    return {
        "number": number,
        "file": path.name,
        "title": tasks.title_of(data, str(number)) or path.stem,
        "status": meta.get("status", ""),
        "session": meta.get("session", ""),
        "created": meta.get("created", ""),
        "sections": {s: data["sections"].get(s, "") for s in tasks.SECTIONS},
        "problems": tasks.problems(path),
        "files": sorted(changed.values(), key=lambda c: c["path"]),
    }


# --- 왼쪽: 파일 구조 ---


def _walk(root: str) -> tuple[list[str], bool]:
    """git 이 아닌 폴더의 파일들 (상대 경로). 상한을 넘으면 (앞부분, True)."""
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        for f in sorted(filenames):
            out.append(f if rel == "." else f"{rel}/{f}")
            if len(out) >= WALK_LIMIT:
                return out, True
    return out, False


def files(key: str) -> dict:
    """왼쪽 파일 칸. 파일마다 생긴 작업 · 최근 작업 · 변경 횟수 (훅의 변경 기록에서만 —
    devdash 이전에 생긴 파일은 기록이 없다)."""
    root = project(key)["root"]
    listed = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    ignored: set[str] = set()
    if listed is not None:
        # 끝이 '/' 인 것은 안쪽의 다른 저장소(폴더)라 파일 목록에서 뺀다
        tracked = {p for p in listed.split("\0") if p and not p.endswith("/")}
        extra, truncated = _ignored_files(root)
        ignored = set(extra) - tracked
        paths = sorted(tracked | ignored)
    else:
        paths, truncated = _walk(root)
    stats: dict[str, dict] = {}
    for rec in _activity(key):
        p = rec.get("path")
        if not p:
            continue
        s = stats.setdefault(p, {"createdTask": None, "lastTask": None, "changes": 0})
        s["changes"] += 1
        if rec.get("op") == "create" and s["createdTask"] is None:
            s["createdTask"] = rec.get("task")
        s["lastTask"] = rec.get("task") or s["lastTask"]
    return {
        "truncated": truncated,
        # ignored: git 에 올리지 않는 파일 (화면이 흐리게 'git 제외'로 보인다)
        "files": [
            {"path": p, **({"ignored": True} if p in ignored else {}), **stats.get(p, {})}
            for p in paths
        ],
    }


def file_history(key: str, path: str) -> list[dict]:
    """[파일] 보기에서 고른 파일을 바꾼 할 일들 (최근 순). 할 일 없이 고친 변경은 task 가 None.
    한 화면에 파일 하나를 크게 보여 주게 되면서 마우스 올림 설명(생긴 · 최근 · 횟수)만으로는
    비어 보여 더했다."""
    by_task: dict[str | None, dict] = {}
    for rec in _activity(key):
        if rec.get("path") != path:
            continue
        task = rec.get("task")
        h = by_task.setdefault(task, {"task": task, "changes": 0, "created": False, "last": None})
        h["changes"] += 1
        h["created"] = h["created"] or rec.get("op") == "create"
        h["last"] = rec.get("at") or h["last"]
    return sorted(by_task.values(), key=lambda h: h["last"] or "", reverse=True)


READ_LIMIT = 1024 * 1024
"""[파일] 보기에서 내용을 보여 줄 파일 크기 상한 (바이트). 문서를 읽으려는 것이라 넉넉하다."""


IMAGE_LIMIT = 10 * 1024 * 1024
"""[파일] 보기 · 문서 속 그림 크기 상한 (바이트). 화면 캡처는 넉넉히 들어가고,
수백 MB 를 잘못 눌러도 브라우저가 멈추지 않게 (사용자 결정)."""

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _project_file(key: str, path: str) -> str:
    """화면이 보낸 저장소 기준 경로를 실제 파일 경로로.
    폴더 밖이면 ValueError, 없으면 FileNotFoundError.

    화면이 보낸 경로를 그대로 믿지 않는다 — '..' · 절대 경로 · 정션으로 폴더 밖을 가리키면
    실제 경로를 풀어 비교해 거절한다. 이 PC 에서만 열리는 화면이지만, 아무 파일이나 읽어 주는
    통로가 되지 않게."""
    root = project(key)["root"]
    full = os.path.realpath(os.path.join(root, path))
    if not store._under(store.same(full), store.same(root)) or store.same(full) == store.same(root):
        raise ValueError("프로젝트 폴더 밖의 파일은 열 수 없습니다")
    if not os.path.isfile(full):
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    return full


def read_image(key: str, path: str) -> tuple[bytes, str]:
    """[파일] 보기 · 문서 속 그림 (내용, Content-Type).
    폴더 밖 · 상한 초과 · 그림이 아니면 ValueError."""
    full = _project_file(key, path)
    kind = IMAGE_TYPES.get(os.path.splitext(full)[1].lower())
    if kind is None:
        raise ValueError("그림 파일(png · jpg · gif · webp · svg · ico)이 아닙니다")
    if os.path.getsize(full) > IMAGE_LIMIT:
        raise ValueError(f"그림이 커서 열지 않습니다 ({IMAGE_LIMIT // (1024 * 1024)}MB 넘음)")
    with open(full, "rb") as f:
        return f.read(), kind


def read_file(key: str, path: str) -> dict:
    """[파일] 보기에서 고른 문서의 내용. 등록된 프로젝트 폴더 안의, 상한 이하 UTF-8 글 파일만."""
    full = _project_file(key, path)
    if os.path.getsize(full) > READ_LIMIT:
        raise ValueError(f"파일이 커서 열지 않습니다 ({READ_LIMIT // 1024}KB 넘음)")
    try:
        with open(full, encoding="utf-8-sig") as f:
            text = f.read()
    except UnicodeDecodeError:
        raise ValueError("글 파일(UTF-8)이 아니라 열 수 없습니다") from None
    return {"path": path, "text": text}


# --- 왼쪽: 세션 ---


def _session_record(session_id: str, key: str) -> dict:
    """훅이 남긴 세션 기록. 다른 프로젝트에서 일했을 수도 있어 이 프로젝트 먼저 본다."""
    for k in [key, *(k for k in store.projects() if k != key)]:
        if (store.project_dir(k) / "sessions" / f"{session_id}.json").exists():
            return store.session_info(k, session_id)
    return {}


_summaries: dict[str, tuple[tuple, dict]] = {}
"""대화 기록 경로 → ((크기, 바뀐 시각), 요약). 화면이 몇 초마다 묻는데 기록은 그보다 드물게
바뀌므로, 파일이 그대로면 다시 읽지 않는다."""
_summaries_lock = threading.Lock()


def _chat_summary(session_id: str) -> dict:
    """대화 기록에서 뽑은 세션 요약 (chat.summary). 기록 파일이 없으면 빈 dict."""
    path = chat.transcript(session_id)
    if path is None:
        return {}
    try:
        st = path.stat()
    except OSError:
        return {}
    stamp = (st.st_size, st.st_mtime_ns)
    with _summaries_lock:
        hit = _summaries.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    value = chat.summary(path)
    with _summaries_lock:
        _summaries[str(path)] = (stamp, value)
    return value


def _home_project(rows: list[dict]) -> dict[str, str | None]:
    """세션마다 작업 폴더로 정해지는 프로젝트 키 (작업용 복사본 · 하위 폴더는 원래 저장소로).
    등록된 프로젝트의 폴더가 아니면 None."""
    roots = {store.same(v["root"]): k for k in store.projects() if (v := project(k))}
    main = _MainPaths()
    return {r["id"]: roots.get(store.same(main(r.get("cwd") or "") or "")) for r in rows}


def _membership(key: str, rows: list[dict]) -> dict[str, tuple[bool, bool]]:
    """세션마다 (이 프로젝트 소속인가, 작업 폴더로 정해진 자동 소속인가).
    사람이 정한 것(row["project"])이 있으면 그것을 따르고, 없으면 작업 폴더를 따른다."""
    home = _home_project(rows)
    out = {}
    for r in rows:
        auto = home[r["id"]] == key
        chosen = r.get("project")
        out[r["id"]] = (auto if chosen is None else chosen == key, auto)
    return out


def sessions(key: str, rows: list[dict]) -> list[dict]:
    """왼쪽 세션 칸: 이 프로젝트 소속 세션 (작업 폴더로 자동 + 사람이 바꾼 것).

    한 줄에 이름 · 지금 하는 일 · 새 답 수 · 시각을 보인다. 새 답 수는 화면이
    '마지막으로 본 시각'과 replies 를 견주어 센다 — 본 시각은 보는 사람(브라우저)마다 다르다.
    rows 는 세션 칸과 같은 줄(SessionService.rows)이라 켜짐/꺼짐 · 토큰이 같은 값이다."""
    member = _membership(key, rows)
    out = []
    for r in rows:
        if not member[r["id"]][0]:
            continue
        conversation = r.get("conversation") or ""
        talk = _chat_summary(conversation)
        out.append(
            {
                "id": r["id"],
                "name": r["name"],
                "group": r.get("group") or "",
                "state": r.get("state", "off"),
                "activity": r.get("activity"),
                # 컨텍스트 토큰 수 · 압축 횟수 — 모니터링 탭의 세션 칸과 같은 값
                "tokens": r.get("tokens"),
                "compactions": r.get("compactions", 0),
                "replies": talk.get("replies", []),
                # 기록 파일을 못 찾으면 훅이 남긴 마지막 답 시각으로
                "lastAt": talk.get("lastAt") or _session_record(conversation, key).get("last_at"),
            }
        )
    return sorted(out, key=lambda s: s["name"].casefold())


def session_choices(key: str, rows: list[dict]) -> list[dict]:
    """[세션 고르기] 창: 세션 전부(그룹별). 지금 소속인 것 · 작업 폴더로 자동인 것 ·
    다른 프로젝트로 정해 둔 것을 표시한다."""
    member = _membership(key, rows)
    home = _home_project(rows)

    def belongs(r: dict) -> str | None:
        chosen = r.get("project")
        return home[r["id"]] if chosen is None else (chosen or None)

    out = [
        {
            "id": r["id"],
            "name": r["name"],
            "group": r.get("group") or "",
            "member": member[r["id"]][0],
            # 다른 프로젝트 소속 (사람이 정했거나 작업 폴더가 그쪽). 고르면 이쪽으로 옮겨진다
            "elsewhere": belongs(r) not in (None, key),
            "auto": member[r["id"]][1],
        }
        for r in rows
    ]
    return sorted(out, key=lambda c: (c["group"].casefold(), c["name"].casefold()))


def set_members(key: str, data: dict, rows: list[dict], ids: list[str]) -> None:
    """고른 세션을 이 프로젝트 소속으로, 빠진 세션은 소속에서 뺀다. 한 세션은 한 프로젝트에만.
    작업 폴더로 정해지는 것과 같으면 따로 적지 않는다 (자동으로 돌린다).

    목록에 없는 번호가 오면 고치지 않고 ValueError — 창을 연 사이 그 세션이 삭제된 것이다.
    그대로 저장하면 그 세션이 조용히 빠진다.
    """
    member = _membership(key, rows)
    stale = [i for i in ids if i not in member]
    if stale:
        raise ValueError("세션 목록이 바뀌었습니다. 창을 다시 열어 주세요")
    chosen = set(ids)
    for r in rows:
        sid = r["id"]
        now, auto = member[sid]
        if sid in chosen:
            registry.set_project(data, sid, None if auto else key)
        elif now:
            # 자동 소속을 뺀 것은 "어디에도 넣지 않음"으로 적어 둬야 다시 자동으로 들어오지 않는다
            registry.set_project(data, sid, "" if auto else None)


# --- 오른쪽: git 이력 ---


FETCH_EVERY = 60
"""초. [git] 을 열어 둔 프로젝트는 이 간격으로 원격의 새 커밋을 받아 온다 (사용자 결정)."""

FETCH_TIMEOUT = 30
"""초. 네트워크가 막혀도 fetch 가 끝없이 붙잡지 않게."""

_fetch_state: dict[str, dict] = {}  # 저장소 폴더 → {"at", "error", "tried"}
_fetch_locks: dict[str, threading.Lock] = {}
_fetch_guard = threading.Lock()


def _run_fetch(root: str) -> dict:
    """git fetch: 원격의 새 커밋 정보만 받아 온다 (origin/main 같은 원격 추적 가지).
    내 가지와 작업 폴더는 건드리지 않아 세션이 하던 일과 섞이지 않는다.

    비밀번호 · 로그인 창이 뜨면 대시보드가 거기서 멈추므로 묻지 않고 실패하게 한다
    (GIT_TERMINAL_PROMPT: 터미널에서 묻기, GCM_INTERACTIVE: 윈도 자격 증명 관리자 창,
    ssh BatchMode: 암호문 · 처음 보는 호스트 키 묻기).
    저장소에 ssh 명령을 따로 정해 두었으면(core.sshCommand) 그대로 쓴다."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    if "GIT_SSH_COMMAND" not in env and not _git(root, "config", "core.sshCommand"):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    with _fetch_guard:
        lock = _fetch_locks.setdefault(root, threading.Lock())
    with lock:  # 같은 저장소에 fetch 가 둘 겹치지 않게
        try:
            r = run_bounded(["git", "-C", root, "fetch", "--quiet"], timeout=FETCH_TIMEOUT, env=env)
            if r is None:
                error = f"{FETCH_TIMEOUT}초 안에 끝나지 않았습니다"
            else:
                lines = r[2].strip().splitlines()
                error = None if r[0] == 0 else (lines[-1] if lines else "가져오지 못했습니다")
        except OSError as exc:
            error = str(exc)
        state = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "error": error,
            "tried": time.time(),
        }
        with _fetch_guard:
            _fetch_state[root] = state
        return state


def _fetch_soon(root: str) -> None:
    """마지막 시도에서 FETCH_EVERY 가 지났으면 뒤에서 fetch 한다.
    화면은 기다리지 않고 지난 결과를 본다."""
    with _fetch_guard:
        last = _fetch_state.get(root)
        if last and time.time() - last["tried"] < FETCH_EVERY:
            return
        lock = _fetch_locks.get(root)
        if lock and lock.locked():
            return  # 앞선 fetch 가 아직 돈다 (원격이 늦다). 줄을 세우지 않는다
        # 5초마다 오는 요청이 그사이 또 띄우지 않게 시도 시각부터 적어 둔다
        _fetch_state[root] = {**(last or {"at": None, "error": None}), "tried": time.time()}
    threading.Thread(target=_run_fetch, args=(root,), daemon=True).start()


def _fetch_info(root: str) -> dict:
    with _fetch_guard:
        state = _fetch_state.get(root) or {}
    return {"at": state.get("at"), "error": state.get("error")}


def fetch(key: str) -> dict:
    """[갱신] 버튼: 지금 바로 fetch 하고 결과를 준다. 원격이 없으면 할 일이 없다."""
    root = project(key)["root"]
    if not (_git(root, "remote") or "").strip():
        return {"at": None, "error": "원격 저장소가 없습니다"}
    state = _run_fetch(root)
    return {"at": state["at"], "error": state["error"]}


def git_history(key: str) -> dict:
    """오른쪽 칸: 최근 커밋(제목의 [T-12] 를 할 일로 잇는다)과 커밋 안 된 변경.
    git 이 아니면 비우고 그렇다고 알린다. 커밋이 아직 없는 저장소는 git 이다.

    동료의 커밋도 보이게: 원격이 있으면 1분마다 fetch 하고, 내 가지와 그 원격 가지
    (upstream, 예: origin/main)의 커밋을 함께 보인다. 아직 pull 하지 않은 커밋은 incoming."""
    root = project(key)["root"]
    if _git(root, "rev-parse", "--is-inside-work-tree") is None:
        return {"git": False, "commits": [], "changes": []}
    has_remote = bool((_git(root, "remote") or "").strip())
    if has_remote:
        _fetch_soon(root)
    upstream = (
        _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or ""
    ).strip()
    heads = ["HEAD", upstream] if upstream else ["HEAD"]
    log = _git(root, "log", f"-n{COMMITS}", "--format=%H%x1f%cI%x1f%an%x1f%s", *heads) or ""
    incoming = (
        set((_git(root, "rev-list", f"HEAD..{upstream}") or "").split()) if upstream else set()
    )
    commits = []
    for line in log.split("\n"):  # splitlines 는 제목 안의 특수 줄바꿈 글자에서도 끊는다
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, at, author, subject = parts
        refs = [int(n) for n in tasks.TASK_REF.findall(subject)]  # 첫 줄(제목)에서만
        commits.append(
            {
                "hash": sha,
                "at": at,
                "author": author,
                "subject": subject,
                "tasks": refs,
                "incoming": sha in incoming,
            }
        )
    status = _git(root, "status", "--porcelain=v1", "-z") or ""
    changes = []
    items = iter(status.split("\0"))
    for item in items:
        if len(item) < 4:
            continue
        code, path = item[:2], item[3:]
        if "R" in code or "C" in code:
            next(items, None)  # 이름 바꾸기 · 복사는 옛 이름이 뒤따른다
        changes.append({"status": code.strip(), "path": path})
    return {
        "git": True,
        "commits": commits,
        "changes": changes,
        "upstream": upstream or None,
        # 원격이 없으면 null — 화면이 [갱신] 버튼을 감춘다
        "fetch": _fetch_info(root) if has_remote else None,
    }


# --- 커밋 하나: 바뀐 파일 · 좌우 비교 ---

COMMIT_ID = re.compile(r"^[0-9a-f]{7,40}$")
"""커밋 번호 모양. 이 모양만 git 에 넘긴다 (--옵션 같은 글자가 명령 인자로 들어가지 않게)."""

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
"""빈 폴더의 git 번호. 첫 커밋은 부모가 없어 이것과 비교한다."""

DIFF_LIMIT = 1024 * 1024
"""바이트. 한 파일의 비교가 이보다 크면 그리지 않는다 (자동 생성 파일 · 큰 데이터)."""

FULL_CONTEXT = 100_000
"""[전체 보기]: 바뀐 곳 앞뒤로 보일 줄 수. 파일 전체가 들어가게 크게."""


def _commit_base(root: str, sha: str) -> tuple[str, str] | None:
    """(커밋 번호, 비교할 부모). 병합 커밋은 첫 부모(합치기 전 내 가지)와 비교한다."""
    out = _git(root, "rev-list", "--parents", "-n1", sha, "--")
    if not out:
        return None
    ids = out.split()
    return ids[0], (ids[1] if len(ids) > 1 else EMPTY_TREE)


def commit_detail(key: str, sha: str) -> dict | None:
    """커밋을 누르면: 제목 · 본문 · 작성자와 바뀐 파일 목록(상태 · 더한 줄 · 지운 줄).
    없으면 None."""
    root = project(key)["root"]
    if not COMMIT_ID.fullmatch(sha) or not (base := _commit_base(root, sha)):
        return None
    full, parent = base
    head = _git(root, "show", "-s", "--format=%an%x00%cI%x00%s%x00%b", full) or ""
    parts = head.split("\0")
    if len(parts) < 4:
        return None
    author, at, subject, body = parts[0], parts[1], parts[2], "\0".join(parts[3:])

    # 상태(A 더함 · M 고침 · D 지움 · R 이름 바꿈 …)와 줄 수를 따로 묻는다.
    # -z: 경로를 따옴표 없이 그대로
    names = (_git(root, "diff", "--name-status", "-z", "-M", parent, full) or "").split("\0")
    files: list[dict] = []
    i = 0
    while i < len(names) and names[i]:
        status = names[i]
        if status[0] in "RC":  # 이름 바꾸기 · 복사는 옛 이름, 새 이름 순
            files.append({"status": status[0], "old": names[i + 1], "path": names[i + 2]})
            i += 3
        else:
            files.append({"status": status[0], "path": names[i + 1]})
            i += 2
    counts: dict[str, tuple[int | None, int | None]] = {}
    nums = iter((_git(root, "diff", "--numstat", "-z", "-M", parent, full) or "").split("\0"))
    for item in nums:
        if not item:
            continue
        added, deleted, path = item.split("\t", 2)
        if not path:  # 이름 바꾸기는 경로가 비고 옛 이름 · 새 이름이 뒤따른다
            next(nums, None)
            path = next(nums, "")
        # 글이 아닌 파일은 줄 수 대신 '-'
        counts[path] = (
            int(added) if added != "-" else None,
            int(deleted) if deleted != "-" else None,
        )
    for f in files:
        f["added"], f["deleted"] = counts.get(f["path"], (None, None))
    return {
        "hash": full,
        "author": author,
        "at": at,
        "subject": subject,
        "body": body.strip(),
        "tasks": [int(n) for n in tasks.TASK_REF.findall(subject)],
        "files": files,
    }


def commit_diff(
    key: str, sha: str, path: str, old: str | None = None, full: bool = False
) -> dict | None:
    """커밋에서 파일 하나가 어떻게 바뀌었나 (gitdiff.parse 모양). 커밋이 없으면 None.
    old: 이름을 바꾼 파일의 옛 이름 (둘 다 넘겨야 git 이 이름 바꾸기로 알아본다)."""
    root = project(key)["root"]
    if not COMMIT_ID.fullmatch(sha) or not (base := _commit_base(root, sha)):
        return None
    commit, parent = base
    paths = [p for p in (old, path) if p]
    out = _git(
        root,
        "--literal-pathspecs",  # 경로의 * · : 같은 글자를 git 이 특수 문법으로 읽지 않게
        "diff",
        "--no-color",
        "--no-ext-diff",
        "-M",
        f"-U{FULL_CONTEXT if full else 3}",
        parent,
        commit,
        "--",
        *paths,
    )
    if out is None:
        return None
    if len(out.encode("utf-8")) > DIFF_LIMIT:
        return {"binary": False, "tooLarge": True, "hunks": []}
    return {**gitdiff.parse(out), "tooLarge": False}


# --- 작업 문서의 바뀐 파일: 이 작업이 그 파일을 어떻게 바꿨나 ---


def snapshot_diff(key: str, before: str | None, after: str | None, full: bool = False) -> dict:
    """두 스냅샷(내용 번호)의 비교 (gitdiff.parse 모양). None 은 '없던 파일 · 지운 파일',
    "unknown" 은 직전 내용을 모름 — 빈 내용과 비교해 지금 내용만 보이고
    beforeUnknown 으로 알린다."""
    project_dir = store.project_dir(key)
    empty = snap.put(project_dir, b"")
    unknown = before == "unknown"
    a = empty if before in (None, "unknown") else before
    b = after or empty
    out = snap._git(
        project_dir, "diff", "--no-color", "--no-ext-diff", f"-U{FULL_CONTEXT if full else 3}", a, b
    ).decode("utf-8", "replace")
    found = {"snapshot": True, "beforeUnknown": unknown, "commits": [], "uncommitted": False}
    if len(out.encode("utf-8")) > DIFF_LIMIT:
        return {**found, "binary": False, "tooLarge": True, "hunks": []}
    return {**found, **gitdiff.parse(out), "tooLarge": False}


_numstat_cache: dict[tuple[str, str | None, str | None], tuple[int | None, int | None]] = {}


def _numstat(key: str, before: str | None, after: str | None) -> tuple[int | None, int | None]:
    """두 스냅샷 사이 (더한 줄, 지운 줄). 글이 아니면 (None, None). 같은 쌍은 기억해 둔다 —
    대화 화면이 몇 초마다 다시 묻는다."""
    hit = _numstat_cache.get((key, before, after))
    if hit is not None:
        return hit
    project_dir = store.project_dir(key)
    empty = snap.put(project_dir, b"")
    a = empty if before in (None, "unknown") else before
    out = snap._git(project_dir, "diff", "--numstat", a, after or empty).decode("utf-8", "replace")
    parts = out.split("\t")
    value = (
        (int(parts[0]), int(parts[1]))
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit()
        else (0, 0)
        if not out.strip()
        else (None, None)
    )
    _numstat_cache[(key, before, after)] = value
    return value


def chat_work(key: str, messages: list[dict]) -> None:
    """대화의 답마다 그 차례에 고친 파일(작업 상자)을 붙인다 — messages 를 고친다.

    원본은 훅의 변경 기록(어느 파일 · 어느 할 일)과 스냅샷(비교 · 줄 수)이다. 대화 기록의
    도구 결과는 Claude 내부 형식이라 쓰지 않는다. 스냅샷이 없는 옛 차례(이 기능 전)는 파일
    이름만 보이고 비교는 없다 (diff False).

    work = {"prompt", "task": {number, title} | None,
            "files": [{path, op: create|edit, added, deleted, diff, shell, agent}]}
    """
    prompts = {m.get("prompt") for m in messages if m.get("role") == "assistant"} - {None}
    if not prompts:
        return
    acts = [r for r in _activity(key) if r.get("prompt") in prompts and r.get("path")]
    if not acts:
        return
    snaps = [e for e in snap.entries(store.project_dir(key)) if e.get("prompt") in prompts]
    titles = {t["number"]: t["title"] for t in task_list(key)}
    for m in messages:
        prompt = m.get("prompt")
        mine = (
            [r for r in acts if r.get("prompt") == prompt] if m.get("role") == "assistant" else []
        )
        if not mine:
            continue
        files = []
        for path in dict.fromkeys(r["path"] for r in mine):
            rows = [r for r in mine if r["path"] == path]
            pair = snap.pair(
                snaps, lambda e, p=path, q=prompt: e.get("prompt") == q and e.get("path") == p
            )
            added = deleted = None
            if pair is not None:
                added, deleted = _numstat(key, *pair)
            files.append(
                {
                    "path": path,
                    "op": "create" if any(r.get("op") == "create" for r in rows) else "edit",
                    "added": added,
                    "deleted": deleted,
                    "diff": pair is not None,
                    "shell": any(r.get("tool") in ("Bash", "PowerShell") for r in rows),
                    # 하위 작업(서브에이전트)만 고친 파일 — 화면이 작게 보인다
                    "agent": all(r.get("agent") for r in rows),
                }
            )
        ref = next((r["task"] for r in mine if r.get("task")), None)
        number = int(ref[2:]) if ref and re.fullmatch(r"T-\d+", ref) else None
        m["work"] = {
            "prompt": prompt,
            "task": {"number": number, "title": titles.get(number, "")} if number else None,
            "files": files,
        }


def shell_tracked(key: str) -> bool:
    """셸 명령으로 고친 파일을 잡을 수 있나 — git 저장소여야 명령 전후 상태를 견준다."""
    proj = project(key)
    return bool(proj) and snap.find_top(proj["root"]) is not None


def turn_diff(key: str, prompt: str, path: str, full: bool = False) -> dict | None:
    """대화 한 차례가 파일 하나를 어떻게 바꿨나: 그 차례에 처음 고치기 직전 → 마지막으로 고친 직후.
    그 차례의 스냅샷이 없으면 None."""
    pair = snap.pair(
        snap.entries(store.project_dir(key)),
        lambda e: e.get("prompt") == prompt and e.get("path") == path,
    )
    return snapshot_diff(key, *pair, full=full) if pair is not None else None


def task_diff(key: str, number: int, path: str, full: bool = False) -> dict:
    """작업 문서의 '이 작업에서 바뀐 파일'을 누르면 보이는 이전 · 이후.

    훅이 남긴 스냅샷이 있으면 그것으로: 이전 = 그 작업이 그 파일을 처음 고치기 직전,
    이후 = 마지막으로 고친 직후. 커밋 메시지 · HEAD 를 보지 않으므로 팀 저장소의 커밋 규칙이나
    git 이 아닌 폴더와 상관없고, 작업이 끝난 뒤 파일이 또 바뀌어도 작업 시점 그대로다.

    스냅샷이 없는 옛 기록(이 기능 전에 끝난 작업)은 커밋으로 정한다 — 기록 규칙이 커밋 제목에
    [T-n] 을 붙인다.
    - 이전: 이 파일을 고친 [T-n] 커밋 중 가장 오래된 것의 직전
    - 이후: 그중 가장 최근 커밋. 진행 중인 작업이고 커밋 안 된 변경이 있으면 지금 파일
    사이에 다른 작업의 커밋이 같은 파일을 고쳤다면 그 변경도 섞인다 (화면이 무엇을 합쳤는지 보인다).

    {"snapshot": bool, "beforeUnknown": bool, "commits": [짧은 번호 …](오래된 것부터),
     "uncommitted": bool, "binary", "tooLarge", "hunks"}.
    비교할 것이 없으면 snapshot · uncommitted 가 False 이고 commits 가 빈다 —
    화면이 [파일] 보기로 연다."""
    task = f"T-{number}"
    pair = snap.pair(
        snap.entries(store.project_dir(key)),
        lambda e: e.get("task") == task and e.get("path") == path,
    )
    if pair is not None:
        return snapshot_diff(key, *pair, full=full)
    return {"snapshot": False, "beforeUnknown": False, **_commit_task_diff(key, number, path, full)}


def _commit_task_diff(key: str, number: int, path: str, full: bool) -> dict:
    """스냅샷이 없는 옛 기록: [T-n] 커밋으로 정한 이전 · 이후."""
    root = project(key)["root"]
    log = _git(root, "--literal-pathspecs", "log", "--format=%H%x1f%s", "--", path) or ""
    mine = []
    for line in log.split("\n"):
        sha, _, subject = line.partition("\x1f")
        if sha and number in {int(n) for n in tasks.TASK_REF.findall(subject)}:
            mine.append(sha)
    mine.reverse()  # 오래된 것부터
    doing = any(r["number"] == number and r["status"] == "doing" for r in task_list(key))
    status = (
        _git(root, "--literal-pathspecs", "status", "--porcelain=v1", "--", path) or ""
    ).strip()
    uncommitted = doing and bool(status)
    found = {"commits": [s[:7] for s in mine], "uncommitted": uncommitted}
    if not mine and not uncommitted:
        return {**found, "binary": False, "tooLarge": False, "hunks": []}

    context = FULL_CONTEXT if full else 3
    if status.startswith("??"):
        # git 이 아직 모르는 새 파일: 빈 파일과 비교한다 (git diff 는 이런 파일을 보지 않는다)
        try:
            with open(os.path.join(root, path), encoding="utf-8") as f:
                text = f.read(DIFF_LIMIT + 1)
        except UnicodeDecodeError:
            return {**found, "binary": True, "tooLarge": False, "hunks": []}
        except OSError:
            return {
                **found,
                "commits": [],
                "uncommitted": False,
                "binary": False,
                "tooLarge": False,
                "hunks": [],
            }
        if len(text) > DIFF_LIMIT:
            return {**found, "binary": False, "tooLarge": True, "hunks": []}
        out = "".join(difflib.unified_diff([], text.splitlines(keepends=True), n=context))
    else:
        base = (_commit_base(root, mine[0]) or (None, EMPTY_TREE))[1] if mine else "HEAD"
        target = [] if uncommitted else [mine[-1]]  # 대상을 비우면 작업 폴더의 지금 파일과 비교한다
        out = _git(
            root,
            "--literal-pathspecs",
            "diff",
            "--no-color",
            "--no-ext-diff",
            "-M",
            f"-U{context}",
            base,
            *target,
            "--",
            path,
        )
        if out is None:
            return {**found, "binary": False, "tooLarge": False, "hunks": []}
    if len(out.encode("utf-8")) > DIFF_LIMIT:
        return {**found, "binary": False, "tooLarge": True, "hunks": []}
    return {**found, **gitdiff.parse(out), "tooLarge": False}


# --- 커밋 AI 요약 ---

_summary_locks: dict[str, threading.Lock] = {}


def _summary_path(key: str, commit: str):
    """커밋 번호별 요약 파일. 저장소 밖(~/.devdash)에 둔다 — 저장소를 더럽히지 않게."""
    return store.project_dir(key) / "summaries" / f"{commit}.json"


def _read_summary(path) -> dict | None:
    """저장된 요약. 없거나 깨졌으면 None.
    깨진 파일 때문에 매번 오류가 나지 않고 다시 만들 수 있게."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("text") else None


def commit_summary(key: str, sha: str, create: bool = False) -> dict | None:
    """저장된 요약 {"text", "model", "at"}. 없으면 text 가 None 이고, create 면 지금 만든다.
    커밋이 없으면 None. 요약을 만들지 못하면 commit_summary.SummaryError."""
    root = project(key)["root"]
    if not COMMIT_ID.fullmatch(sha) or not (base := _commit_base(root, sha)):
        return None
    commit, parent = base
    path = _summary_path(key, commit)
    if saved := _read_summary(path):
        return saved
    if not create:
        return {"text": None, "model": None, "at": None}
    with _fetch_guard:
        lock = _summary_locks.setdefault(commit, threading.Lock())
    # 화면이 같은 커밋을 두 번 물어도(다시 그리기 · 두 창) 한 번만 만든다.
    # 뒤에 온 쪽은 기다렸다가 저장된 것을 읽는다
    with lock:
        if saved := _read_summary(path):
            return saved
        detail = commit_detail(key, commit)
        patch = _git(root, "diff", "--no-color", "--no-ext-diff", "-M", parent, commit)
        # 커밋 정보나 diff 를 못 받으면(큰 커밋에서 시간 초과 등) 만들지 않는다 — 빈 diff 로 만든
        # 요약이 저장되면 다시 만들 길이 없다
        if detail is None or patch is None:
            raise commit_summary_ai.SummaryError(
                "커밋 내용을 읽지 못했습니다. 잠시 뒤 다시 시도해 주세요"
            )
        text = commit_summary_ai.summarize(detail, patch)
        value = {
            "text": text,
            "model": commit_summary_ai.MODEL,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        _write_json(path, value)
        return value
