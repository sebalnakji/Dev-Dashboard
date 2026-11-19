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

셸 명령(Bash)으로 고친 파일은 미리 알 수 없어 직전 내용을 뜰 수 없다. Claude 가 명령 결과에 남기는
바뀐 부분(bashEditDiff 의 hunks)을 지금 내용에 거꾸로 적용해 직전 내용을 만든다.

훅에서 불리므로 표준 라이브러리와 git 명령만 쓴다.
옛 기록(activity.jsonl 등)의 형식은 바꾸지 않는다.
"""

from __future__ import annotations

import json
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
