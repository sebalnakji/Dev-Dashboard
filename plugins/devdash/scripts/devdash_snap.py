"""작업 · 차례별 바뀐 파일 비교를 위한 스냅샷 (훅이 남기고 대시보드가 읽는다).

    ~/.devdash/projects/<키>/
        snapshots.git     파일 내용 (스냅샷 전용 bare git 저장소 —
                          작업 중인 저장소는 건드리지 않는다)
        snapshots.jsonl   한 줄 = 한 스냅샷
                          {at, session, prompt, task, path, worktree, when, blob, tool}
                          when: "before"(고치기 직전) | "after"(고친 직후)
                          blob: 내용 번호. before 에서 None 은 "없던 파일",
                          "unknown" 은 "알 수 없음"

커밋 메시지 · HEAD 에 기대지 않는다 — 팀 저장소의 커밋 규칙과 상관없고, git 이 아닌 폴더에서도 된다.
같은 내용은 git 이 한 번만 저장하므로 크기 상한 없이도 작게 유지된다.

- 작업 비교: (작업, 파일)의 첫 before → 마지막 after
- 차례 비교: (차례, 파일)의 첫 before → 마지막 after

셸 명령(Bash)으로 고친 파일은 무엇을 고칠지 미리 알 수 없다. git 저장소면 명령 직전 · 직후의
git status 를 견준다 (사용자 결정): 직전에 깨끗했던 파일의 직전 내용은 HEAD, 이미 고쳐져 있던
파일은 명령 직전에 떠 둔 내용. 확실히 읽기만 하는 명령(ls · cat · git log …)은 건너뛴다.
git 이 아닌 폴더는 Claude 가 결과에 남기는 바뀐 부분(bashEditDiff — 내부 형식이라 늘 오지는
않는다)이 있을 때만, 지금 내용에 거꾸로 적용해 직전 내용을 만든다.

훅에서 불리므로 표준 라이브러리와 git 명령만 쓴다.
옛 기록(activity.jsonl 등)의 형식은 바꾸지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

GIT_TIMEOUT = 10.0
"""초. 훅이 세션을 오래 붙잡지 않게."""

GIT = ["git", "-c", "core.longpaths=true"]
"""윈도우에서 저장소 경로가 길면(약 250자) 'Filename too long' 으로 실패한다 —
프로젝트 키가 경로를 담아 길어지기 쉽다."""


REPO_NAME = "snapshots.git"


def repo(project_dir: Path) -> Path:
    return project_dir / REPO_NAME


def log_path(project_dir: Path) -> Path:
    return project_dir / "snapshots.jsonl"


def _git(project_dir: Path, *args: str, data: bytes | None = None) -> bytes:
    """스냅샷 저장소에 git 명령. 실패하면 RuntimeError (훅이 삼킨다).

    프로젝트 기록 폴더에서 상대 경로로 부른다 — 절대 경로로 주면 긴 경로에서 git 이
    "'$GIT_DIR' too big" 으로 거절한다 (core.longpaths 를 켜도)."""
    r = subprocess.run(
        [*GIT, f"--git-dir={REPO_NAME}", *args],
        cwd=project_dir,
        input=data,
        capture_output=True,
        timeout=GIT_TIMEOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip())
    return r.stdout


def ensure_repo(project_dir: Path) -> None:
    if not (repo(project_dir) / "HEAD").exists():
        project_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [*GIT, "init", "--bare", "-q", REPO_NAME],
            cwd=project_dir,
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=True,
        )


def put(project_dir: Path, content: bytes) -> str:
    """내용을 넣고 번호를 돌려준다. 같은 내용은 같은 번호 — 다시 넣어도 저장소가 늘지 않는다."""
    ensure_repo(project_dir)
    return _git(project_dir, "hash-object", "-w", "--stdin", data=content).decode().strip()


def get(project_dir: Path, blob: str) -> bytes:
    return _git(project_dir, "cat-file", "blob", blob)


def read_file(path: str | Path) -> bytes | None:
    """지금 파일 내용. 없으면 None."""
    try:
        return Path(path).read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return None


def entries(project_dir: Path) -> list[dict]:
    """스냅샷 기록 전체. 깨진 줄은 건너뛴다."""
    path = log_path(project_dir)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def pair(items: list[dict], match) -> tuple[str | None, str | None] | None:
    """조건(match)에 맞는 스냅샷들의 (첫 before 의 blob, 마지막 after 의 blob). 없으면 None.
    before 가 없으면 None 을 넣지 않고 "unknown" — 직전 내용을 모른다."""
    before = after = None
    seen_before = seen_after = False
    for e in items:
        if not match(e):
            continue
        if e.get("when") == "before" and not seen_before:
            before, seen_before = e.get("blob"), True
        elif e.get("when") == "after":
            after, seen_after = e.get("blob"), True
    if not seen_after:
        return None
    return (before if seen_before else "unknown"), after


# --- 셸로 고친 파일: 바뀐 부분을 거꾸로 적용해 직전 내용 만들기 ---


def reverse_patch(after: str, hunks: list[dict]) -> str | None:
    """지금 내용(after)에 바뀐 부분(hunks — structuredPatch 모양)을 거꾸로 적용한 직전 내용.
    지금 내용이 바뀐 부분과 맞지 않으면(그 사이 다른 것이 파일을 바꿈) None."""
    lines = after.split("\n")
    # 뒤 덩어리부터 되돌려야 앞 덩어리의 줄 번호가 흔들리지 않는다
    for hunk in sorted(hunks, key=lambda h: h.get("newStart", 0), reverse=True):
        body = hunk.get("lines")
        if not isinstance(body, list):
            return None
        new = [x[1:] for x in body if isinstance(x, str) and x[:1] in (" ", "+")]
        old = [x[1:] for x in body if isinstance(x, str) and x[:1] in (" ", "-")]
        start = max(int(hunk.get("newStart", 1)) - 1, 0)
        if int(hunk.get("newLines", len(new))) == 0:
            start = int(hunk.get("newStart", 0))  # 0줄 덩어리의 시작은 '그 줄 뒤' 를 가리킨다
        if lines[start : start + len(new)] != new:
            return None
        lines[start : start + len(new)] = old
    return "\n".join(lines)


def bash_files(tool_response: object) -> list[dict]:
    """셸 명령 결과에서 바뀐 파일들 [{filePath, hunks}].
    Claude 내부 형식이라 없거나 모양이 다르면 []."""
    if not isinstance(tool_response, dict):
        return []
    diff = tool_response.get("bashEditDiff")
    files = diff.get("files") if isinstance(diff, dict) else None
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, dict) and isinstance(f.get("filePath"), str)]


# --- 셸 명령: 읽기만 하는 명령 가리기 · git 상태 ---

READ_ONLY = frozenset(
    [
        "ls",
        "dir",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "ag",
        "fd",
        "echo",
        "printf",
        "pwd",
        "which",
        "where",
        "type",
        "file",
        "stat",
        "du",
        "df",
        "tree",
        "less",
        "more",
        "uniq",
        "cut",
        "diff",
        "cmp",
        "date",
        "whoami",
        "printenv",
        "true",
        "false",
        "test",
        "[",
        "realpath",
        "readlink",
        "dirname",
        "basename",
        "md5sum",
        "sha1sum",
        "sha256sum",
        "nl",
        "column",
        "find",
        "sort",
        "jq",
        "ps",
        "tasklist",
        "hostname",
        "uname",
        "id",
        "sleep",
        "seq",
        "tr",
        "comm",
        "od",
        "xxd",
        "hexdump",
        "strings",
        "cd",
    ]
)
"""파일을 바꾸지 않는 명령. 여기 없거나 애매하면 읽기 전용으로 보지 않는다 (사용자 결정)."""

GIT_READ_ONLY = frozenset(
    [
        "status",
        "log",
        "diff",
        "show",
        "blame",
        "grep",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "describe",
        "shortlog",
        "cat-file",
        "whatchanged",
        "name-rev",
        "merge-base",
        "rev-list",
        "count-objects",
        "for-each-ref",
        "show-ref",
        "ls-remote",
        "check-ignore",
        "var",
        "version",
        "help",
    ]
)

_PARTS = re.compile(r"&&|\|\||[;|&\n]")
_HARMLESS_REDIRECT = re.compile(r"\d?>&\d|\d?>\s*(/dev/null|nul|\$null)\b", re.IGNORECASE)


def read_only(command: object) -> bool:
    """확실히 읽기만 하는 셸 명령인가. 이어 붙인 명령(&& ; |)은 모두 그래야 한다.
    파일로 내보내기(>) · 명령 치환($( ) ` `) · 모르는 명령이 하나라도 있으면 False."""
    if not isinstance(command, str) or not command.strip():
        return False
    if "`" in command or "$(" in command or "<(" in command:
        return False
    text = _HARMLESS_REDIRECT.sub(" ", command)
    if ">" in text:
        return False
    for part in _PARTS.split(text):
        words = part.split()
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words.pop(0)  # FOO=1 명령
        if not words:
            continue
        name = os.path.basename(words[0].strip("\"'")).lower().removesuffix(".exe")
        args = words[1:]
        if name == "git":
            rest = list(args)
            while rest and rest[0].startswith("-"):
                flag = rest.pop(0)
                if flag in ("-C", "-c") and rest:
                    rest.pop(0)
            if rest and rest[0] in GIT_READ_ONLY:
                continue
            return False
        if name not in READ_ONLY:
            return False
        if name == "find" and any(
            a in ("-exec", "-execdir", "-delete", "-ok", "-fprint") for a in args
        ):
            return False
        if name == "sort" and any(a.startswith(("-o", "--output")) for a in args):
            return False
    return True


def find_top(path: str | Path | None) -> Path | None:
    """path 를 품은 git 저장소(작업용 복사본 포함)의 맨 위 폴더.
    git 을 부르지 않고 .git 을 찾는다."""
    if not path:
        return None
    p = Path(os.path.abspath(path))
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def status(top: Path) -> dict[str, str]:
    """{저장소 기준 경로: 상태 두 글자}. 바뀐 · 새 파일(무시 목록 제외)만.
    --no-optional-locks: 사용자의 git 명령과 index.lock 으로 부딪치지 않게."""
    r = subprocess.run(
        [
            *GIT,
            "--no-optional-locks",
            "-C",
            str(top),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--ignore-submodules",
        ],
        capture_output=True,
        timeout=GIT_TIMEOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip())
    out = {}
    for item in r.stdout.decode("utf-8", "replace").split("\0"):
        if len(item) > 3:
            out[item[3:]] = item[:2]
    return out


def head_content(top: Path, rel: str) -> bytes | None:
    """마지막 커밋의 그 파일 내용. 커밋에 없으면 None."""
    r = subprocess.run(
        [*GIT, "--no-optional-locks", "-C", str(top), "cat-file", "blob", f"HEAD:{rel}"],
        capture_output=True,
        timeout=GIT_TIMEOUT,
    )
    return r.stdout if r.returncode == 0 else None


def put_paths(project_dir: Path, paths: list[str]) -> list[str]:
    """여러 파일을 한 번에 넣는다 (git 한 번). 돌려주는 순서는 paths 와 같다."""
    if not paths:
        return []
    ensure_repo(project_dir)
    out = _git(project_dir, "hash-object", "-w", "--stdin-paths", data="\n".join(paths).encode())
    return out.decode().split()


def file_stat(path: str | Path) -> list[int] | None:
    """[수정 시각(ns), 크기]. 없거나 파일이 아니면 None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_mtime_ns, st.st_size] if os.path.isfile(path) else None
