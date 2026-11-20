"""저장소 밖 기록 위치(~/.devdash)와 프로젝트 등록.

    ~/.devdash/projects/<키>/
        project.json     등록 정보 {"root": 저장소 경로, "name": 표시 이름}
        tasks/           할 일 파일 (T-12-제목.md)
        activity.jsonl   자동 변경 기록 (훅이 한 줄씩 덧붙임)
        sessions/<세션 번호>.json   세션 이름 · 마지막 작업

훅과 대시보드가 같이 쓴다. 훅에서 불리므로 표준 라이브러리만 쓴다.
DEVDASH_HOME 환경 변수로 위치를 바꿀 수 있다 (테스트용).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

LOCK_WAIT = 3.0
"""초. 잠금을 이 안에 못 얻으면 포기한다 — 훅이 세션을 오래 붙잡지 않게."""


def home() -> Path:
    return Path(os.environ.get("DEVDASH_HOME") or Path.home() / ".devdash")


def norm(path: str | Path) -> str:
    """경로 표기를 맞춘다: 슬래시, 끝 슬래시 없음, '..' 정리, 드라이브 글자 대문자. 저장·표시용."""
    p = os.path.normpath(str(path)).replace("\\", "/").rstrip("/")
    return p[0].upper() + p[1:] if len(p) > 1 and p[1] == ":" else p


def same(path: str | Path) -> str:
    """비교용 경로: 실제 경로(정션·링크 풀기) + 대소문자 무시. 윈도우는 대소문자를 가리지 않는다.
    'cd c:\\projects\\…' 로 시작한 세션의 경로도 등록 경로와 같게 본다."""
    return norm(os.path.realpath(str(path))).casefold()


def project_key(root: str | Path) -> str:
    """저장소 경로를 폴더 이름으로: 'C:/Projects/TOY/Dev' → 'C--Projects-TOY-Dev-1a2b3c'.
    끝의 지문은 'C:/a b' · 'C:/a-b' 처럼 글자만 바꾸면 같아지는 경로를 가르기 위한 것.
    앞부분은 디스크의 실제 표기로 만든다 — 'c:/projects/dev' 로 등록해도 같은 폴더가 되게
    (입력 표기를 그대로 쓰면 지문은 같고 앞부분만 달라 폴더가 둘로 갈렸다)."""
    slug = re.sub(r"[^A-Za-z0-9가-힣_.-]", "-", norm(os.path.realpath(str(root))))
    return f"{slug}-{hashlib.sha1(same(root).encode('utf-8')).hexdigest()[:6]}"


def project_dir(key: str) -> Path:
    return home() / "projects" / key


def register(root: str | Path, name: str | None = None) -> str:
    """프로젝트를 등록하고 키를 돌려준다. 이미 있으면 그대로 둔다."""
    root = norm(root)
    key = project_key(root)
    d = project_dir(key)
    meta = d / "project.json"
    if meta.exists():
        existing = json.loads(meta.read_text(encoding="utf-8")).get("root", "")
        if same(existing) != same(root):
            raise ValueError(f"같은 키({key})를 다른 경로가 쓰고 있습니다: {existing}")
    (d / "tasks").mkdir(parents=True, exist_ok=True)
    (d / "sessions").mkdir(exist_ok=True)
    if not meta.exists():
        data = {"root": root, "name": name or root.rsplit("/", 1)[-1]}
        meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return key


def projects() -> dict[str, dict]:
    """등록된 프로젝트 {키: project.json 내용}."""
    out: dict[str, dict] = {}
    base = home() / "projects"
    if not base.is_dir():
        return out
    for d in base.iterdir():
        try:
            out[d.name] = json.loads((d / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return out


def _git(path: str, *args: str) -> str | None:
    """git 명령의 출력 한 줄. git 이 아니거나 실패하면 None.

    출력은 UTF-8 로 읽는다 — 기본(cp949)으로 읽으면 한글 경로에서 깨져 작업용 복사본을 못 찾는다
    (테스트에서 실제로 걸림).
    """
    try:
        r = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def _git_main_root(path: str) -> str | None:
    """작업용 복사본(worktree)이어도 원래 저장소의 경로. git 이 아니면 None."""
    common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    # 원래 저장소의 .git 폴더 → 그 부모가 원래 저장소
    return norm(Path(common).parent) if common and Path(common).name == ".git" else None


def _under(path_cmp: str, root_cmp: str) -> bool:
    return path_cmp == root_cmp or path_cmp.startswith(root_cmp + "/")


def find_project(*paths: str | None) -> tuple[str, str] | None:
    """주어진 경로(세션의 시작 폴더 · 작업 폴더 등)가 속한 등록 프로젝트 (키, 루트).

    먼저 경로가 등록된 루트 안에 있는지(가장 긴 것) 보고, 없으면 git 으로 원래 저장소를 찾는다.
    작업용 복사본이 저장소 안(.claude/worktrees)이면 첫 단계에서, 밖이면 둘째 단계에서 찾는다.
    """
    registered = {
        same(v["root"]): (k, norm(v["root"])) for k, v in projects().items() if v.get("root")
    }
    if not registered:
        return None
    candidates = list(dict.fromkeys(p for p in paths if p))  # 같은 경로를 두 번 보지 않는다
    for p in candidates:
        hits = [r for r in registered if _under(same(p), r)]
        if hits:
            return registered[max(hits, key=len)]
    for p in candidates:
        main = _git_main_root(p)
        if main and same(main) in registered:
            return registered[same(main)]
    return None


def worktree_root(path: str) -> str | None:
    """파일이 든 git 작업 트리(작업용 복사본이면 그 복사본)의 루트."""
    top = _git(path, "rev-parse", "--show-toplevel")
    return norm(top) if top else None


def relative_in_project(file_path: str, root: str) -> tuple[str, str | None] | None:
    """파일 경로를 (저장소 기준 상대 경로, 작업용 복사본 이름) 으로.

    작업용 복사본 안의 파일은 원래 저장소 기준 경로로 바꾼다 — 같은 파일이 복사본마다 다른 이름으로
    보이지 않게. 프로젝트 밖 파일(메모리 · 임시 파일 등)이면 None. 비교는 대소문자 무시, 돌려주는
    경로는 원래 표기를 지킨다.
    """
    f = norm(file_path)
    # 실제 표기로 맞춘 뒤 비교하고 자른다 — 정션 · 짧은 이름(A-RATH~1)으로 들어온 경로를
    # git 이 준 경로의 길이로 자르면 빈 경로가 된다
    real_f = norm(os.path.realpath(f))
    parent = real_f.rsplit("/", 1)[0]
    top = worktree_root(parent) if Path(parent).is_dir() else None
    real_top = norm(os.path.realpath(top)) if top else None
    if real_top and same(real_top) != same(root) and _under(same(real_f), same(real_top)):
        main = _git_main_root(real_top)
        if main and same(main) == same(root):
            return real_f[len(real_top) + 1 :], real_top.rsplit("/", 1)[-1]
        # 그 밖(등록 폴더가 큰 저장소의 하위 폴더, 안쪽에 다른 저장소)은 아래 경로 비교로 판단한다
    real_root = norm(os.path.realpath(root))
    if not real_f.casefold().startswith(real_root.casefold() + "/"):
        return None
    # 자를 때는 원래 표기의 길이로 (casefold 는 글자 수가 바뀔 수 있다: ß → ss)
    rel = real_f[len(real_root) + 1 :]
    # 저장소 안의 .claude/worktrees/<이름>/ (복사본이 지워져 git 으로 못 찾을 때)
    m = re.match(r"^\.claude/worktrees/([^/]+)/(.+)$", rel)
    return (m.group(2), m.group(1)) if m else (rel, None)


@contextlib.contextmanager
def locked(path: Path, wait: float | None = None, busy: str | None = None) -> Iterator[None]:
    """다른 프로세스와 겹치지 않게 path 옆 잠금 파일을 쥔다.

    윈도우의 '끝에 덧붙이기'는 한 번에 일어나지 않아, 여러 세션이 동시에 쓰면 줄이 사라지고 깨진다
    (직접 재현: 6개 프로세스 1,800줄 → 1,377줄). 그래서 쓰기 전에 잠근다.
    wait(기본 LOCK_WAIT) 안에 못 쥐면 TimeoutError(busy 또는 기본 문구) — 부르는 쪽(훅)이 삼킨다.
    대시보드의 세션 목록(registry)도 이 함수로 잠근다.
    """
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + (LOCK_WAIT if wait is None else wait)
        while True:
            try:
                _lock(fd)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(busy or f"잠금을 얻지 못했습니다: {lock}") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


if os.name == "nt":
    import msvcrt

    def _lock(fd: int) -> None:
        """기다리지 않고 잠근다 — 못 쥐면 OSError. 윈도우는 첫 바이트 하나를 잠근다."""
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        """기다리지 않고 잠근다 — 못 쥐면 OSError(BlockingIOError)."""
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def append_activity(key: str, record: dict) -> None:
    """변경 기록 한 줄을 잠근 채 덧붙인다."""
    path = project_dir(key) / "activity.jsonl"
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8", "replace")
    with locked(path), open(path, "ab") as f:
        f.write(line)


def read_activity(key: str) -> list[dict]:
    """변경 기록 전체. 깨진 줄(잠금 도입 전 기록 등)은 건너뛴다."""
    path = project_dir(key) / "activity.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def session_info(key: str, session_id: str) -> dict:
    try:
        return json.loads((project_dir(key) / "sessions" / f"{session_id}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def update_session(key: str, session_id: str, **fields) -> None:
    """세션 정보(이름 · 마지막 작업 등)를 갱신한다. 세션마다 파일 하나라 서로 겹치지 않는다.
    대시보드가 읽는 중이면 바꿔치기가 잠깐 실패할 수 있어 몇 번 다시 해 본다."""
    d = project_dir(key) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session_id}.json"
    data = session_info(key, session_id)
    data.update({k: v for k, v in fields.items() if v is not None})
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(text.encode("utf-8", "replace"))  # 이상한 글자(고립 대리쌍)가 있어도 저장
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def claim_name(key: str, session_id: str, wanted: str) -> str:
    """세션에 이름을 붙이고 그 이름을 돌려준다. 다른 세션이 이미 쓰면 'wanted_2' 처럼 가른다.

    할 일의 주인을 이름으로 가리므로 이름이 겹치면 한 세션이 다른 세션의 할 일을 가져가거나
    기록이 섞인다 (복사본 세션이 같은 이름을 받는 것을 실제로 확인).
    살펴보기와 쓰기 사이에 다른 세션이 끼지 않게 잠근다.
    """
    d = project_dir(key) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    with locked(project_dir(key) / "names"):
        taken = set()
        for p in d.glob("*.json"):
            if p.stem != session_id:
                taken.add(session_info(key, p.stem).get("name"))
        name, n = wanted, 1
        while name in taken:
            n += 1
            name = f"{wanted}_{n}"
        update_session(key, session_id, name=name)
    return name


def next_auto_name(key: str) -> str:
    """이름 없는 세션에 붙일 이름: '<프로젝트 이름>_001'. 순번은 프로젝트마다 하나씩 늘린다."""
    name = projects().get(key, {}).get("name") or key
    counter = project_dir(key) / "auto-name-counter"
    with locked(counter):
        try:
            n = int(counter.read_text(encoding="utf-8").strip() or 0) + 1
        except (OSError, ValueError):
            n = 1
        counter.write_text(str(n), encoding="utf-8")
    return f"{name}_{n:03d}"


def main_path(path: str) -> str:
    """등록할 폴더: git 저장소면 원래 저장소의 맨 위 폴더, 아니면 그 폴더 그대로.

    작업용 복사본(.claude/worktrees/x) 이나 하위 폴더에서 불러도 원래 저장소를 등록한다.
    복사본은 지워지고, 하위 폴더를 등록하면 복사본에서 일하는 세션이 프로젝트를 못 찾아
    기록이 조용히 꺼졌다. 모노레포의 한 폴더만 등록하는 일은 지금 하지 않는다.
    """
    return _git_main_root(path) or norm(os.path.realpath(path))


def main(argv: list[str]) -> int:
    """/devdash:init 이 부르는 명령: register <폴더> [표시 이름]. 결과를 Claude 와 사람이 읽는다.
    표시 이름이 비어 있으면("") 폴더 이름을 쓴다."""
    if len(argv) not in (3, 4) or argv[1] != "register":
        print("사용법: devdash_store.py register <폴더> [표시 이름]")
        return 2
    root = main_path(argv[2])
    key = project_key(root)
    existed = key in projects()
    try:
        register(root, name=argv[3] if len(argv) == 4 else None)
    except ValueError as exc:
        print(f"등록하지 못했습니다: {exc}")
        return 0  # 0 이 아니면 스킬 전체가 멈춰 이 이유가 사용자에게 안 간다
    info = projects()[key]
    if existed:
        print(f"이미 등록되어 있습니다: {info['root']} (이름: {info['name']}, 이름은 바꾸지 않음)")
    else:
        print(f"등록했습니다: {info['root']} (이름: {info['name']})")
    print(f"작업 기록 폴더: {project_dir(key) / 'tasks'}")
    return 0


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv))
