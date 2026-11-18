"""claude 명령을 다룬다 — 세션 목록 · 이어 띄우기 · 새로 만들기 · 지우기 · 관리자 명단 확인.

세션은 **목록 번호**(`claude agents` 의 id, 8자리)로 가리킨다. 이름은 겹칠 수 있고(같은 이름의
옛 판이 여럿 남는다), 대화 번호(sessionId)는 /clear 마다 바뀐다. 목록 번호만 그대로다.

여기 있는 주의점은 모두 실제로 겪은 것이다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from devdash.home import claude_dir

CLAUDE_HOME = claude_dir()
ROSTER = CLAUDE_HOME / "daemon" / "roster.json"
JOBS = CLAUDE_HOME / "jobs"

_MARKERS = ("CLAUDECODE", "CLAUDE_PID")
_MARKER_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_JOB", "CLAUDE_BG_")


def clean_env(env: dict | None = None) -> dict:
    """Claude 세션 안에서 실행됐다는 표시(환경 변수)를 뺀 환경.

    이 프로그램을 Claude 세션 안에서 띄우면 그 표시를 물려받고, 여기서 띄우는 claude 까지
    그대로 물려받는다. 그러면 새 claude 가 자기를 '하위 세션'으로 여겨 대화 기록 저장을 끄고
    세션 목록에도 등록하지 않는다 ('Transcript saving is off — inherited
    CLAUDE_CODE_CHILD_SESSION marker'). CLAUDE_CONFIG_DIR 처럼 사용자가 둔 설정은 남긴다.
    """
    env = dict(os.environ if env is None else env)
    return {
        k: v for k, v in env.items() if k not in _MARKERS and not k.startswith(_MARKER_PREFIXES)
    }


def drop_claude_markers() -> None:
    """이 프로세스의 환경에서 표시를 지운다 (serve 가 시작할 때). clean_env 참고."""
    keep = clean_env()
    for k in list(os.environ):
        if k not in keep:
            del os.environ[k]


_CLAUDE: str | None = None


def claude_exe() -> str:
    global _CLAUDE
    if _CLAUDE is None:
        _CLAUDE = shutil.which("claude") or shutil.which("claude.cmd")
        if not _CLAUDE:
            raise FileNotFoundError("claude 를 PATH 에서 찾지 못했습니다.")
    return _CLAUDE


def _run(args: list[str], cwd: str | None = None, timeout: float = 120) -> tuple[int, str]:
    """claude 를 실행해 (종료 코드, 표준 출력 + 표준 오류). 표시를 지운 환경으로 띄운다."""
    try:
        r = subprocess.run(
            [claude_exe(), *args],
            cwd=cwd,
            env=clean_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return -1, f"claude 가 {timeout:.0f}초 안에 끝나지 않았습니다"
    except OSError as exc:
        return -1, f"claude 를 실행하지 못했습니다: {exc}"
    return r.returncode, f"{r.stdout or ''}\n{r.stderr or ''}".strip()


def run_json(args: list[str]) -> list:
    """claude 하위 명령을 실행해 JSON 배열을 읽는다. 실패하면 빈 목록."""
    _, txt = _run(args)
    # 표준 오류의 경고가 뒤에 붙을 수 있어 대괄호 범위만 읽는다
    s, e = txt.find("["), txt.rfind("]")
    if 0 <= s < e:
        try:
            data = json.loads(txt[s : e + 1])
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return []


def live_sessions() -> list:
    """켜진 세션 · 최근에 끊긴 세션 · 대화형(터미널) 세션."""
    return run_json(["agents", "--json"])


def all_sessions() -> list:
    """백그라운드 세션 전부. 오래 꺼진 세션은 여기에만 `state: done` 으로 남는다.
    꺼진 행의 sessionId 는 **처음 대화 번호**다 — 이어 열 때 이것을 쓴다 (/clear 뒤에도)."""
    return run_json(["agents", "--all", "--json"])


def last_conversation(session_id: str) -> str | None:
    """관리자가 기억하는 이 세션의 대화 번호 (내부 파일 jobs/<목록 번호>/state.json).
    꺼진 세션의 대화 · 토큰을 보일 때 쓴다. 못 읽으면 None."""
    try:
        state = json.loads((JOBS / session_id / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = state.get("sessionId") if isinstance(state, dict) else None
    return value if isinstance(value, str) and value else None


def explain(returncode: int, output: str) -> str | None:
    """claude --bg 의 결과를 실패 이유로 바꾼다. 성공이면 None."""
    if "Workspace not trusted" in output:
        # 폴더 이름을 바꾸면 Claude 에겐 처음 보는 폴더라 다시 승인해야 한다
        return (
            "이 폴더를 아직 신뢰하지 않았습니다. 그 폴더에서 claude 를 한 번 실행해 "
            "신뢰를 승인한 뒤 다시 해 주세요"
        )
    if "started a copy" in output:
        return "원본 대신 복사본이 생겼습니다. claude agents 로 확인해 주세요"
    if returncode != 0:
        first = next((ln.strip() for ln in output.splitlines() if ln.strip()), "")
        return first or f"claude 가 실패했습니다 (코드 {returncode})"
    return None


def resume(cwd: str, conversation: str) -> str | None:
    """대화를 백그라운드로 이어 띄운다. 실패하면 이유를 돌려준다.

    꺼진 세션은 **처음 대화 번호**로 이어야 같은 목록 번호 · 이름 · 옵션으로 되살아난다.
    /clear 뒤의 새 대화 번호로 이으면 대화는 맞게 잇지만 새 목록 번호가 생기고 이름을 잃는다.
    플래그(--name 등)를 붙이거나 번호를 8자리로 줄여도 복사본이 생긴다.
    그래서 전체 번호에 --resume 만 쓰고, 항상 세션의 작업 폴더에서 실행한다.
    """
    return explain(*_run(["--bg", "--resume", conversation], cwd=cwd))


def create(cwd: str, name: str) -> str | None:
    """새 백그라운드 세션을 만든다. 질문은 보내지 않는다. 실패하면 이유를 돌려준다.

    원격 제어(--remote-control)를 붙인다 — 붙이지 않으면 Claude 앱 사이드바에 보이지 않는다
    (2.1.327 에서 확인: 앱에 보이는 세션은 모두 원격 연결이 있다). 실행 옵션으로 저장되어
    해제 뒤 다시 연결해도 이어진다."""
    return explain(*_run(["--bg", "--name", name, "--remote-control"], cwd=cwd))


def remove(session_id: str) -> str | None:
    """목록에서 지운다 (claude rm). 대화 기록 파일은 남는다. 실패하면 이유를 돌려준다."""
    code, out = _run(["rm", session_id])
    if code == 0:
        return None
    if "worktree" in out.lower():
        # 커밋 안 된 변경이 있는 작업용 복사본을 가진 기록은 claude 가 지우지 않는다
        return "커밋하지 않은 변경이 있는 작업용 복사본(worktree)이 있어 지우지 않았습니다"
    first = next((ln.strip() for ln in out.splitlines() if ln.strip()), "")
    return first or f"claude rm 이 실패했습니다 (코드 {code})"


def wait_for(match, limit: float = 25, interval: float = 2, *, live: bool = False) -> dict | None:
    """목록에서 match(행) 가 참인 행이 나타날 때까지 기다린다. limit 초 안에 없으면 None."""
    end = time.time() + limit
    while True:
        rows = live_sessions() if live else all_sessions()
        hit = next((r for r in rows if match(r)), None)
        if hit or time.time() > end:
            return hit
        time.sleep(interval)


def confirm_started(session_id: str, limit: float = 25) -> str | None:
    """이어 띄운 세션이 실제로 떴는지 확인한다. 실패면 이유를 돌려준다.

    대화 기록이 없으면 claude --bg --resume 은 성공(0)으로 끝나고, 세션이 십여 초 뒤
    'failed' 로 바뀐다. 그래서 명령 결과만으론 알 수 없어 상태를 본다. 예전 실패가 남긴
    'failed' 기록과 헷갈리지 않게, 프로세스가 생기면 바로 성공으로 보고 끝까지 안 생기고
    'failed' 일 때만 실패로 본다.
    """
    row = wait_for(lambda r: r.get("id") == session_id and r.get("pid"), limit)
    if row:
        return None
    rows = [r for r in all_sessions() if r.get("id") == session_id]
    if any(r.get("state") == "failed" for r in rows):
        return "세션이 뜨자마자 실패했습니다. 대화 기록을 찾지 못했을 수 있습니다"
    return None  # 판단할 수 없으면 '연결 중' 표시의 제한 시간에 맡긴다


def in_roster(conversation: str) -> bool:
    """백그라운드 관리자가 아직 이 세션을 '실행 중'으로 쥐고 있는가.

    프로세스를 끊으면 claude agents 에선 곧바로 pid 가 빠지지만, 관리자 명단에선 약 5초 뒤에
    빠진다. 그 사이 --resume 하면 '이미 실행 중'으로 보고 복사본을 만든다.
    내부 파일이라 형식을 해석하지 않고 번호가 들어 있는지만 본다.
    """
    if not conversation:
        return False
    try:
        workers = json.loads(ROSTER.read_text(encoding="utf-8")).get("workers", {})
    except (OSError, ValueError):
        return False
    return conversation in json.dumps(workers)


def wait_roster_clear(conversation: str, limit: float = 20) -> bool:
    """관리자 명단에서 빠질 때까지 기다린다. limit 초 안에 빠지면 True."""
    end = time.time() + limit
    while in_roster(conversation):
        if time.time() > end:
            return False
        time.sleep(1)
    return True


def bg_task_count(session_id: str) -> int | None:
    """세션이 띄워 둔 백그라운드 작업(서버 등) 수. 내부 파일이라 못 읽으면 None."""
    try:
        state = json.loads((JOBS / session_id / "state.json").read_text(encoding="utf-8"))
        return int(state["inFlight"]["tasks"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


# --- 폴더 ---


def claude_json() -> Path:
    """Claude 의 사용자 상태 파일. CLAUDE_CONFIG_DIR 이 있으면 그 안, 없으면 홈의 .claude.json."""
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return claude_dir() / ".claude.json"
    return Path.home() / ".claude.json"


def trusted_folders() -> list[str]:
    """Claude 가 신뢰를 승인한 폴더들. 새 세션의 폴더 후보로 보인다. 지금 없는 폴더는 뺀다."""
    try:
        projects = json.loads(claude_json().read_text(encoding="utf-8")).get("projects") or {}
    except (OSError, ValueError, AttributeError):
        return []
    return [
        path
        for path, info in projects.items()
        if isinstance(info, dict) and info.get("hasTrustDialogAccepted") and os.path.isdir(path)
    ]


def folder_label(cwd: str) -> str:
    """세션의 작업 폴더 이름 (마지막 칸). 이름 없는 세션을 가리킬 때 쓴다."""
    return Path(cwd).name if cwd else ""
