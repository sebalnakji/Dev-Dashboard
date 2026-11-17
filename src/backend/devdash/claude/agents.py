"""claude 명령을 다룬다 — 세션 조회 · 이어 띄우기 · 관리자 명단 확인.

여기 있는 주의점은 모두 실제로 겪은 것이다. 자세한 경위는 docs/logs 와
docs/decisions/0005 를 본다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
ROSTER = CLAUDE_HOME / "daemon" / "roster.json"
JOBS = CLAUDE_HOME / "jobs"


def drop_claude_markers() -> None:
    """Claude 세션 안에서 실행됐다는 표시(환경 변수)를 지운다.

    이 프로그램을 Claude 세션 안에서 띄우면 그 표시를 물려받고, 여기서 띄우는 claude 까지
    그대로 물려받는다. 그러면 새 claude 가 자기를 '하위 세션'으로 여겨 대화 기록 저장을 끄고
    세션 목록에도 등록하지 않는다 ('Transcript saving is off — inherited
    CLAUDE_CODE_CHILD_SESSION marker'). CLAUDE_CONFIG_DIR 처럼 사용자가 둔 설정은 남긴다.
    """
    for k in list(os.environ):
        if k in ("CLAUDECODE", "CLAUDE_PID") or k.startswith(
            ("CLAUDE_CODE_", "CLAUDE_JOB", "CLAUDE_BG_")
        ):
            del os.environ[k]


_CLAUDE: str | None = None


def claude_exe() -> str:
    global _CLAUDE
    if _CLAUDE is None:
        _CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")
        if not _CLAUDE:
            raise FileNotFoundError("claude 를 PATH 에서 찾지 못했습니다.")
    return _CLAUDE


def run_json(args: list[str]) -> list:
    """claude 하위 명령을 실행해 JSON 배열을 읽는다. 실패하면 빈 목록."""
    try:
        r = subprocess.run(
            [claude_exe(), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    txt = (r.stdout or "").strip()
    try:
        return json.loads(txt) if txt else []
    except json.JSONDecodeError:
        # 앞뒤에 잡음이 섞였을 때 대괄호 범위만 다시 시도한다
        s, e = txt.find("["), txt.rfind("]")
        if 0 <= s < e:
            try:
                return json.loads(txt[s : e + 1])
            except json.JSONDecodeError:
                pass
    return []


def live_sessions() -> list:
    return run_json(["agents", "--json"])


def all_sessions() -> list:
    """끝난 백그라운드 기록까지 포함한 목록."""
    return run_json(["agents", "--all", "--json"])


def find_live(entry: dict, live: list) -> dict | None:
    """목록 항목에 해당하는 실행 중 세션을 찾는다.

    pid 가 없으면 목록엔 있어도 실제로는 안 도는 기록이다 — 살아있다고 보지 않는다.
    되살리거나 /clear 하면 번호가 바뀔 수 있어 이름도 함께 대조한다.
    """
    eid, ename = entry["id"], entry.get("name") or ""
    for s in live:
        if s.get("pid") is None:
            continue
        if s.get("sessionId", "").startswith(eid):
            return s
        nm = s.get("name") or ""
        if nm and (nm in (ename, eid, eid[:8]) or (len(nm) >= 8 and eid.startswith(nm))):
            return s
    return None


def explain_resume(returncode: int, output: str) -> str | None:
    """claude --bg --resume 의 결과를 실패 이유로 바꾼다. 성공이면 None."""
    if "Workspace not trusted" in output:
        # 폴더 이름을 바꾸면 Claude 에겐 처음 보는 폴더라 다시 승인해야 한다
        return (
            "이 폴더를 아직 신뢰하지 않았습니다. 그 폴더에서 claude 를 한 번 실행해 "
            "신뢰를 승인한 뒤 다시 눌러 주세요"
        )
    if "started a copy" in output:
        return "원본 대신 복사본이 생겼습니다. claude agents 로 확인해 주세요"
    if returncode != 0:
        first = next((ln.strip() for ln in output.splitlines() if ln.strip()), "")
        return first or f"claude 가 실패했습니다 (코드 {returncode})"
    return None


def resume(entry: dict) -> str | None:
    """꺼진 세션을 백그라운드로 이어 띄운다. 실패하면 이유를 돌려준다.

    플래그(--name 등)를 붙이거나 번호를 8자리로 줄이면 원본을 잇지 않고 복사본이 생긴다.
    그래서 전체 번호에 --resume 만 쓰고, 항상 프로젝트 폴더에서 실행한다.
    """
    r = subprocess.run(
        [claude_exe(), "--bg", "--resume", entry["id"]],
        cwd=entry["project"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return explain_resume(r.returncode, f"{r.stdout or ''}\n{r.stderr or ''}")


def confirm_started(entry: dict, limit: float = 25) -> str | None:
    """띄운 세션이 실제로 떴는지 확인한다. 실패면 이유를 돌려준다.

    대화 기록이 없으면 claude --bg --resume 은 성공(0)으로 끝나고, 세션이 십여 초 뒤
    'failed' 로 바뀐다. 그래서 명령 결과만으론 알 수 없어 상태를 본다. 예전 실패가 남긴
    'failed' 기록과 헷갈리지 않게, 프로세스가 생기면 바로 성공으로 보고 끝까지 안 생기고
    'failed' 일 때만 실패로 본다.
    """
    end = time.time() + limit
    while True:
        recs = [s for s in all_sessions() if s.get("sessionId") == entry["id"]]
        if any(s.get("pid") for s in recs):
            return None
        if time.time() > end:
            if any(s.get("state") == "failed" for s in recs):
                return "세션이 뜨자마자 실패했습니다. 대화 기록을 찾지 못했을 수 있습니다"
            return None  # 판단할 수 없으면 '연결 중' 표시의 제한 시간에 맡긴다
        time.sleep(2)


def in_roster(session_id: str) -> bool:
    """백그라운드 관리자가 아직 이 세션을 '실행 중'으로 쥐고 있는가.

    프로세스를 끊으면 claude agents 에선 곧바로 pid 가 빠지지만, 관리자 명단에선 약 5초 뒤에
    빠진다. 그 사이 --resume 하면 '이미 실행 중'으로 보고 복사본을 만든다.
    내부 파일이라 형식을 해석하지 않고 번호가 들어 있는지만 본다.
    """
    if not session_id:
        return False
    try:
        workers = json.loads(ROSTER.read_text(encoding="utf-8")).get("workers", {})
    except (OSError, ValueError):
        return False
    return session_id in json.dumps(workers)


def wait_roster_clear(session_id: str, limit: float = 20) -> bool:
    """관리자 명단에서 빠질 때까지 기다린다. limit 초 안에 빠지면 True."""
    end = time.time() + limit
    while in_roster(session_id):
        if time.time() > end:
            return False
        time.sleep(1)
    return True


def bg_task_count(record: dict) -> int | None:
    """세션이 띄워 둔 백그라운드 작업(서버 등) 수. 내부 파일이라 못 읽으면 None."""
    try:
        state = json.loads((JOBS / record["id"] / "state.json").read_text(encoding="utf-8"))
        return int(state["inFlight"]["tasks"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
