"""Claude Code 호환 시험 — `devdash check-claude`.

이 앱은 Claude Code 의 **공식 계약이 아닌 동작**에 기댄다: `claude agents` 의 JSON 모양,
이어 열기 규칙, 대화 기록 형식, 관리자의 내부 파일(jobs · roster). Claude Code 는 자주 새 버전이
나오고 실제로 이 동작이 바뀐 적이 있어서(/clear 뒤 이어 열기, 앱 사이드바 표시),
한 벌의 시험을 두고 버전마다 다시 돌린다.

- 시험 세션은 이름이 `devdash-compat-` 로 시작하고, 시험 폴더 `~/.devdash/compat` 에서만 띄운다.
  사용자의 세션 · 설정은 건드리지 않고, 끝나면 시험 폴더의 세션 · 대화 기록 · 훅 설정을 모두 지운다
- 질문은 두 번: 시험 세션의 첫 질문(가장 싼 모델 — /clear 시험에 대화 내용이 필요하다)과
  `claude -p` 한 번
- 결과는 `~/.devdash/compat.json`. 대시보드는 지금 버전이 마지막으로 통과한 버전과 다르면 알린다

앞부분은 출력을 보고 무엇이 틀렸는지 한 줄로 답하는 검사 규칙(가짜 출력으로 시험한다),
뒷부분(Runner)이 실제 claude 를 돌린다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from devdash.claude import agents, chat
from devdash.home import REPO, claude_dir, home

PREFIX = "devdash-compat-"
MODEL = "haiku"
WORD = "바나나"
PROMPT = "compat.txt 파일을 Read 도구로 읽고, 그 안에 든 낱말 하나만 답해."

TITLES = {
    1: "세션 목록",
    2: "새로 만들기",
    3: "이어 열기",
    4: "끄기",
    5: "/clear",
    6: "대화 기록 위치",
    7: "대화 기록 내용",
    8: "수정 기록",
    9: "훅 입력",
    10: "플러그인 명령",
    11: "하위 세션 표시",
    12: "백그라운드 작업 수",
    13: "대화 없이 실행",
    14: "버전",
    15: "삭제 · 복원",
    16: "대화 보존",
    17: "이름 출처",
}


def compat_dir() -> Path:
    return home() / "compat"


def results_path() -> Path:
    return home() / "compat.json"


@dataclass
class Item:
    num: int
    title: str
    ok: bool | None = None
    """None 이면 건너뜀 (시험할 거리가 없었다)."""
    reason: str = ""


# --- 검사 규칙 (출력 → 틀렸으면 한 줄 이유, 맞으면 None) ---------------------------------

BG_KEYS = ("id", "sessionId", "cwd", "kind", "state")


def check_rows(live: object, every: object) -> str | None:
    """1. `claude agents --json` · `--all --json` 의 모양."""
    if not isinstance(live, list) or not isinstance(every, list):
        return "목록이 JSON 배열이 아니다"
    for r in every:
        if not isinstance(r, dict):
            return "--all 의 행이 객체가 아니다"
        missing = [k for k in BG_KEYS if k not in r]
        if missing:
            return f"--all 행({r.get('id')})에 {', '.join(missing)} 가 없다"
    for r in live:
        if not isinstance(r, dict):
            return "--json 의 행이 객체가 아니다"
        if r.get("kind") == "interactive":
            if r.get("id"):
                return "대화형 행에 목록 번호(id)가 있다 — 보기만 하는 처리가 맞지 않는다"
            if not r.get("sessionId"):
                return "대화형 행에 sessionId 가 없다"
            continue
        missing = [k for k in BG_KEYS if k not in r]
        if missing:
            return f"--json 행({r.get('id')})에 {', '.join(missing)} 가 없다"
    return None


def parse_version(text: str) -> str | None:
    """14. `claude --version` 출력에서 번호."""
    m = re.search(r"\b(\d+\.\d+\.\d+)\b", text or "")
    return m.group(1) if m else None


HOOK_KEYS = {
    "SessionStart": ("source", "session_id", "cwd", "session_title"),
    "UserPromptSubmit": ("session_id", "prompt_id"),
    "PostToolUse": ("tool_name", "tool_input", "tool_response"),
    "Stop": ("last_assistant_message", "stop_hook_active"),
    "SessionEnd": ("reason",),
}
"""devdash 플러그인 훅이 읽는 입력."""


def check_hooks(captured: dict[str, list[dict]]) -> str | None:
    """9. 훅 입력의 모양. /clear 의 끝은 SessionEnd(reason=clear) 로 온다."""
    absent = [event for event in HOOK_KEYS if not captured.get(event)]
    if absent:
        return f"{', '.join(absent)} 훅이 오지 않았다"
    for event, keys in HOOK_KEYS.items():
        missing = [k for k in keys if not any(k in g for g in captured[event])]
        if missing:
            return f"{event} 훅 입력에 {', '.join(missing)} 가 없다"
    if not any(g.get("reason") == "clear" for g in captured["SessionEnd"]):
        return "/clear 때 SessionEnd 의 reason 이 clear 가 아니다"
    return None


USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def check_transcript(lines: list[dict]) -> str | None:
    """7. 대화 기록 한 벌의 모양 (세션 대화 · 토큰 수가 읽는 것)."""
    users = [x for x in lines if x.get("type") == "user" and not x.get("isMeta")]
    answers = [x for x in lines if x.get("type") == "assistant"]
    if not users or not answers:
        return "대화 기록에 user · assistant 줄이 없다"
    for key in ("isSidechain", "timestamp", "message"):
        if not all(key in u for u in users):
            return f"user 줄에 {key} 가 없다"
    if not any("promptId" in u for u in users):
        return "user 줄에 promptId 가 없다"
    usages = [(a.get("message") or {}).get("usage") for a in answers]
    usages = [u for u in usages if isinstance(u, dict)]
    if not usages:
        return "assistant 줄에 usage 가 없다"
    for key in USAGE_KEYS:
        if not any(key in u for u in usages):
            return f"usage 에 {key} 가 없다"
    return None


def check_compactions(lines: list[dict]) -> str | None:
    """7. 압축 기록의 모양 (압축 횟수 · 압축 뒤 토큰 어림). 압축 기록이 있는 대화에서만 본다."""
    for x in lines:
        if x.get("type") == "system" and x.get("subtype") == "compact_boundary":
            post = (x.get("compactMetadata") or {}).get("postTokens")
            if not isinstance(post, int):
                return "압축 기록(compact_boundary)에 compactMetadata.postTokens 가 없다"
    return None


def check_edit_results(results: list[dict]) -> str | None:
    """8. 수정 도구 결과(toolUseResult)의 모양."""
    for r in results:
        if "filePath" not in r:
            return "수정 결과에 filePath 가 없다"
        if "oldString" in r and ("newString" not in r or "structuredPatch" not in r):
            return "Edit 결과에 newString · structuredPatch 가 없다"
        if r.get("type") in ("create", "update") and "content" not in r:
            return "Write 결과에 content 가 없다"
    return None


def check_plugin_lists(marketplaces: object, plugins: object) -> str | None:
    """10. `plugin marketplace list --json` · `plugin list --json` 의 모양 (setup 이 읽는 것)."""
    if not isinstance(marketplaces, list) or not all(
        isinstance(m, dict) and "name" in m and ("path" in m or "installLocation" in m)
        for m in marketplaces
    ):
        return "marketplace list --json 에 name · path 가 없다"
    if not isinstance(plugins, list) or not all(
        isinstance(p, dict) and "id" in p and "enabled" in p for p in plugins
    ):
        return "plugin list --json 에 id · enabled 가 없다"
    return None


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def transcript_folder(cwd: str | Path) -> str:
    """6. 대화 기록 폴더 이름 규칙: 경로의 글자 · 숫자가 아닌 것을 모두 '-' 로."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def summary_line(item: Item) -> str:
    mark = {True: "통과", False: "실패", None: "건너뜀"}[item.ok]
    reason = f" — {item.reason}" if item.reason else ""
    return f"  {mark}  {item.num:>2}. {item.title}{reason}"


# --- 결과 파일 ------------------------------------------------------------------------


def load_results() -> dict:
    try:
        data = json.loads(results_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_results(version: str | None, items: list[Item]) -> dict:
    """이번 결과를 적는다. 모두 통과(건너뜀 포함)면 그 버전을 '마지막으로 통과한 버전'으로."""
    passed = all(i.ok is not False for i in items)
    old = load_results()
    data = {
        "version": version,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "passed": passed,
        "passedVersion": version if passed else old.get("passedVersion"),
        "items": [asdict(i) for i in items],
    }
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


_version_cache: tuple[float, str | None] = (0.0, None)
VERSION_CACHE_SECONDS = 600


def installed_version(fresh: bool = False) -> str | None:
    """지금 깔린 claude 의 버전 (10분 기억 — 세션 칸이 3초마다 묻는다). fresh 면 새로 묻는다."""
    global _version_cache
    if fresh or time.time() - _version_cache[0] > VERSION_CACHE_SECONDS:
        code, out = agents._run(["--version"], timeout=30)
        _version_cache = (time.time(), parse_version(out) if code == 0 else None)
    return _version_cache[1]


def running_path() -> Path:
    return home() / "compat.running.json"


def running() -> dict | None:
    """돌고 있는 호환 시험 {pid, version, at} — 없거나 그 프로세스가 이미 끝났으면 None."""
    import psutil

    try:
        data = json.loads(running_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("pid"), int)
        and psutil.pid_exists(data["pid"])
    ):
        return data
    running_path().unlink(missing_ok=True)  # 멈춘 시험이 남긴 표시
    return None


def auto_error_path() -> Path:
    return home() / "compat-auto.json"


def load_auto_error() -> dict:
    try:
        data = json.loads(auto_error_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_auto_error(version: str, reason: str, output: str = "") -> None:
    """자동 시험을 시작하지도 못한 이유 (시험 폴더 신뢰 등). 같은 버전은 되풀이하지 않는다."""
    path = auto_error_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": version,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reason": reason,
        "output": output[-2000:],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def status() -> dict:
    """대시보드가 보이는 것: 지금 버전 · 마지막으로 통과한 버전 · 마지막 시험 · 돌고 있나 ·
    자동 시험 설정 · 지금 버전에서 실패한 항목 · 자동 시험을 시작하지 못한 이유."""
    from devdash import prefs

    data = load_results()
    version = installed_version()
    failures = []
    if data and data.get("version") == version and not data.get("passed"):
        failures = [
            {"num": i.get("num"), "title": i.get("title"), "reason": i.get("reason", "")}
            for i in data.get("items") or []
            if i.get("ok") is False
        ]
    auto_error = load_auto_error()
    return {
        "version": version,
        "passedVersion": data.get("passedVersion"),
        "lastRun": {k: data.get(k) for k in ("version", "at", "passed")} if data else None,
        "running": running() is not None,
        "auto": bool(prefs.get("autoCompat")),
        "failures": failures,
        "autoError": auto_error.get("reason") if auto_error.get("version") == version else None,
    }


# --- 실제로 돌리기 --------------------------------------------------------------------

REAL_CLAUDE_FILES = (
    "settings.json",
    "plugins/known_marketplaces.json",
    "plugins/installed_plugins.json",
)
"""플러그인 명령 시험 뒤 그대로여야 하는 실제 Claude 설정 파일."""

_BG_ID = re.compile(r"backgrounded\s*·\s*([0-9a-f]{8})")

HOOK_SCRIPT = """import pathlib, sys
d = pathlib.Path(__file__).with_name('.devdash-hooks')
d.mkdir(exist_ok=True)
# 훅 입력은 UTF-8 이다 — 윈도우 기본(cp949)으로 읽으면 한글이 든 입력에서 죽는다
text = sys.stdin.buffer.read().decode('utf-8', 'replace').strip()
with (d / (sys.argv[1] + '.jsonl')).open('a', encoding='utf-8') as f:
    f.write(text + '\\n')
"""


def _fingerprint(root: Path) -> dict[str, str | None]:
    out = {}
    for rel in REAL_CLAUDE_FILES:
        p = root / rel
        out[rel] = hashlib.sha1(p.read_bytes()).hexdigest() if p.exists() else None
    return out


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    out.append(entry)
                if limit and len(out) >= limit:
                    break
    except OSError:
        pass
    return out


class Runner:
    """시험 폴더에서 시험 세션을 띄워 항목을 차례로 확인한다. 끝나면 시험 폴더를 비운다."""

    def __init__(self, say: Callable[[str], None] = print):
        self.say = say
        self.items = {n: Item(n, t) for n, t in TITLES.items()}
        self.cwd = compat_dir()
        self.env = agents.clean_env()
        self.hooks = self.cwd / ".devdash-hooks"
        self.name = PREFIX + "a"
        self.a: str | None = None  # 시험 세션의 목록 번호
        self.first: str | None = None  # 그 처음 대화 번호
        self.fake_resume_failed: bool | None = None
        self.untrusted: Path | None = None

    # --- 바탕 ---

    def claude(self, *args: str, cwd: Path | None = None, env: dict | None = None, timeout=180):
        try:
            r = subprocess.run(
                [agents.claude_exe(), *args],
                cwd=str(cwd or self.cwd),
                env=env or self.env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return -1, f"{timeout:.0f}초 안에 끝나지 않았다"
        return r.returncode, f"{r.stdout or ''}\n{r.stderr or ''}".strip()

    def json_list(self, *args: str, env: dict | None = None) -> object:
        _, out = self.claude(*args, env=env)
        s, e = out.find("["), out.rfind("]")
        try:
            return json.loads(out[s : e + 1]) if 0 <= s < e else None
        except ValueError:
            return None

    def every(self) -> list[dict]:
        rows = self.json_list("agents", "--all", "--json")
        return rows if isinstance(rows, list) else []

    def row(self, sid: str | None) -> dict | None:
        return next((r for r in self.every() if r.get("id") == sid), None) if sid else None

    def mine(self, rows: list[dict] | None = None) -> list[dict]:
        """시험 폴더(와 신뢰 안 된 시험 폴더)에서 띄운 세션."""
        places = [self.cwd] + ([self.untrusted] if self.untrusted else [])
        return [
            r
            for r in (self.every() if rows is None else rows)
            if any(same_path(r.get("cwd") or "", p) for p in places)
        ]

    @staticmethod
    def wait(cond: Callable[[], object], limit: float, step: float = 2) -> object:
        end = time.time() + limit
        while True:
            value = cond()
            if value or time.time() > end:
                return value
            time.sleep(step)

    def kill(self, sid: str) -> bool:
        """[해제]처럼 프로세스 트리를 끊고, 목록 · 관리자 명단에서 빠질 때까지 기다린다."""
        from devdash.local import kill_tree

        r = self.row(sid)
        if r and r.get("pid"):
            kill_tree(r["pid"])
        gone = self.wait(lambda: not (self.row(sid) or {}).get("pid"), 20)
        conv = (r or {}).get("sessionId") or ""
        released = self.wait(lambda: not agents.in_roster(conv), 30)
        time.sleep(3)  # 빠진 직후 바로 이어 열면 복사본이 생긴 적이 있다
        return bool(gone and released)

    def stop(self, sid: str) -> bool:
        """claude stop 으로 끈다. /clear 를 실어 깨운 세션은 목록 상태가 한동안 working 이라, 이때
        프로세스를 끊으면 관리자가 '죽었다'(crashed)로 보고 다시 띄운다 (2.1.328 확인)."""
        conv = (self.row(sid) or {}).get("sessionId") or ""
        self.claude("stop", sid)
        gone = self.wait(lambda: not (self.row(sid) or {}).get("pid"), 30)
        released = self.wait(lambda: not agents.in_roster(conv), 30)
        time.sleep(3)
        return bool(gone and released)

    def set(self, num: int, ok: bool | None, reason: str = "") -> None:
        item = self.items[num]
        item.ok, item.reason = ok, reason
        self.say(summary_line(item))

    def trusted(self) -> bool:
        try:
            projects = json.loads(agents.claude_json().read_text(encoding="utf-8")).get("projects")
        except (OSError, ValueError, AttributeError):
            return False
        return any(
            same_path(k, self.cwd) and isinstance(v, dict) and v.get("hasTrustDialogAccepted")
            for k, v in (projects or {}).items()
        )

    def captured(self) -> dict[str, list[dict]]:
        return {p.stem: _read_jsonl(p) for p in self.hooks.glob("*.jsonl")}

    # --- 준비 · 정리 ---

    def preflight(self) -> str | None:
        if not shutil.which("claude"):
            return "claude 명령을 찾지 못했습니다. Claude Code 를 설치한 뒤 다시 하세요."
        self.cwd.mkdir(parents=True, exist_ok=True)
        if not self.trusted():
            return (
                "시험 폴더를 Claude 가 아직 신뢰하지 않았습니다. 한 번만 이렇게 승인해 주세요:\n"
                f'  cd "{self.cwd}"\n  claude\n  (신뢰 질문에 "예" → /exit)'
            )
        return None

    def prepare(self) -> None:
        """지난 시험이 중간에 멈춰 남은 것을 치우고, 낱말 파일과 시험 폴더 전용 훅을 둔다."""
        self.cleanup()
        (self.cwd / "compat.txt").write_text(WORD + "\n", encoding="utf-8")
        script = self.cwd / ".devdash-hooklog.py"
        script.write_text(HOOK_SCRIPT, encoding="utf-8")
        command = f'"{sys.executable}" "{script}"'
        hooks = {
            event: [{"hooks": [{"type": "command", "command": f"{command} {event}"}]}]
            for event in HOOK_KEYS
        }
        settings = self.cwd / ".claude" / "settings.json"
        settings.parent.mkdir(exist_ok=True)
        settings.write_text(json.dumps({"hooks": hooks}), encoding="utf-8")

    def cleanup(self) -> None:
        """시험 폴더의 세션을 끄고 지운 뒤, 그 대화 기록 · 훅 설정 · 낱말 파일을 지운다."""
        from devdash.local import kill_tree

        for _ in range(2):  # 대화가 없는 세션은 끊으면 다시 뜬다 — 한 번 더 지운다
            rows = self.mine()
            for r in rows:
                if r.get("pid"):
                    kill_tree(r["pid"])
            for r in rows:
                self.claude("rm", r["id"])
            if rows:
                time.sleep(20)
        folder = claude_dir() / "projects" / transcript_folder(self.cwd)
        if folder.is_dir():
            for p in folder.iterdir():
                if p.name != "memory":  # Claude 가 폴더마다 두는 기억 — 시험과 무관
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
        for name in (".devdash-hooks", ".claude"):
            shutil.rmtree(self.cwd / name, ignore_errors=True)
        for name in (".devdash-hooklog.py", "compat.txt"):
            (self.cwd / name).unlink(missing_ok=True)
        if self.untrusted:
            shutil.rmtree(self.untrusted, ignore_errors=True)

    # --- 항목 ---

    def step_version(self) -> str | None:
        code, out = self.claude("--version")
        version = parse_version(out) if code == 0 else None
        self.set(14, bool(version), version or f"번호를 읽지 못했다: {out[:60]}")
        return version

    def step_create(self) -> None:
        # 원격 제어는 붙이지 않는다 — 붙이면 시험할 때마다 Claude 앱의 '최근 항목' 에 시험 세션이
        # 하나씩 남고, 그 기록은 claude.ai 쪽이라 사람이 하나씩 지워야 한다.
        # 그래서 '앱 사이드바에 보이는지' 는 시험하지 않는다
        code, out = self.claude(
            "--bg",
            "--name",
            self.name,
            "--model",
            MODEL,
            # '=' 로 붙인다 — 띄어 쓰면 --allowedTools 가 뒤의 질문까지 도구 이름으로 먹는다
            "--allowedTools=Read",
            PROMPT,
        )
        m = _BG_ID.search(out)
        if code != 0 or not m:
            self.set(2, False, f"띄우지 못했다: {out.splitlines()[0] if out else code}")
            return
        self.a = m.group(1)
        row = self.wait(lambda: (r := self.row(self.a)) and r.get("pid") and r, 40)
        # 첫 질문(Read 한 번 + 짧은 답)이 끝나야 다음 시험을 한다 — Stop 훅으로 안다
        done = self.wait(lambda: (self.hooks / "Stop.jsonl").exists(), 180, 3)
        problems = []
        if not row:
            problems.append("목록에 켜진 행이 나타나지 않았다")
        else:
            self.first = row.get("sessionId")
            if row.get("name") != self.name:
                problems.append(f"이름이 {row.get('name')!r}")
            if not same_path(row.get("cwd") or "", self.cwd):
                problems.append("작업 폴더가 다르다")
            if row.get("kind") != "background":
                problems.append(f"kind 가 {row.get('kind')!r}")
        if not done:
            problems.append("첫 질문이 3분 안에 끝나지 않았다")
        # 신뢰 승인이 없는 폴더는 거절해야 한다 (새 세션 창의 안내가 이 문구에 기댄다)
        self.untrusted = home() / f"compat-untrusted-{uuid.uuid4().hex[:8]}"
        self.untrusted.mkdir(parents=True)
        _, out = self.claude("--bg", "--name", PREFIX + "untrusted", cwd=self.untrusted)
        if "Workspace not trusted" not in out:
            problems.append("신뢰 안 된 폴더에서 Workspace not trusted 로 거절하지 않았다")
        self.set(2, not problems, "; ".join(problems))

    def step_list(self) -> None:
        live = self.json_list("agents", "--json")
        every = self.json_list("agents", "--all", "--json")
        reason = check_rows(live, every)
        if reason is None:
            ours = [r for r in every if r.get("id") == self.a]
            if not ours or not any(r.get("id") == self.a for r in live):
                reason = "시험 세션이 --json · --all 에 다 보이지 않는다"
            elif not ours[0].get("pid"):
                reason = "켜진 시험 세션의 pid 가 없다"
        self.set(1, reason is None, reason or "")

    def transcript_path(self, conversation: str) -> Path:
        return claude_dir() / "projects" / transcript_folder(self.cwd) / f"{conversation}.jsonl"

    def step_transcript(self) -> None:
        path = self.transcript_path(self.first or "")
        found = chat.transcript(self.first or "")
        if path.exists() and found and same_path(found, path):
            self.set(6, True)
        else:
            self.set(6, False, f"있어야 할 곳에 없다: {path}")
        # 표시를 지운 채 띄웠으니 기록이 남아야 한다 (물려받으면 '하위 세션'이 되어 기록을 끈다)
        self.set(
            11, path.exists(), "" if path.exists() else "표시를 지운 채 띄웠는데 대화 기록이 없다"
        )
        lines = _read_jsonl(path)
        reason = check_transcript(lines)
        if reason is None:
            compacted = [
                p for p in self.recent_transcripts(30) if b"compact_boundary" in p.read_bytes()
            ]
            reason = next((r for p in compacted if (r := check_compactions(_read_jsonl(p)))), None)
            note = "" if compacted else "압축 기록은 최근 대화에 없어 보지 못함"
            self.set(7, reason is None, reason or note)
        else:
            self.set(7, False, reason)

    def recent_transcripts(self, n: int) -> list[Path]:
        files = (claude_dir() / "projects").glob("*/*.jsonl")
        try:
            return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:n]
        except OSError:
            return []

    def step_edits(self) -> None:
        """사용자의 최근 대화 기록에서 수정 도구 결과를 읽어 모양만 본다 (읽기만)."""
        results: list[dict] = []
        for path in self.recent_transcripts(20):
            for entry in _read_jsonl(path):
                r = entry.get("toolUseResult")
                if (
                    isinstance(r, dict)
                    and "filePath" in r
                    and ("oldString" in r or r.get("type") in ("create", "update"))
                ):
                    results.append(r)
            if len(results) >= 20:
                break
        if not results:
            self.set(8, None, "최근 대화 기록에 수정 결과가 없다")
            return
        reason = check_edit_results(results)
        self.set(8, reason is None, reason or "")

    def step_jobs(self) -> None:
        try:
            state = json.loads((agents.JOBS / (self.a or "") / "state.json").read_text("utf-8"))
            ok = isinstance(state["inFlight"]["tasks"], int)
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        self.set(
            12,
            ok,
            ""
            if ok
            else "jobs/<번호>/state.json 에 inFlight.tasks 가 없다 (해제 확인 문구만 빠진다)",
        )

    def step_name(self) -> None:
        """이름 출처: 관리자가 스스로 다시 띄울 때는 실행 옵션의 --name 만 쓰고, 사람이 이어 열 때는
        대화 기록의 이름(custom-title)을 읽는다. 대시보드는 둘 다에 기댄다."""
        problems = []
        flags = agents.respawn_flags(self.a or "")
        if self.name not in flags or "--name" not in flags:
            problems.append(f"실행 옵션에 --name 이 없다: {flags}")
        if chat.title(self.first or "") != self.name:
            problems.append("대화 기록에 이름(custom-title)이 없다")
        row = self.row(self.a) or {}
        if row.get("name") != self.name:
            problems.append(f"목록 이름이 {row.get('name')!r}")
        self.set(17, not problems, "; ".join(problems))

    def step_kill(self) -> None:
        r = self.row(self.a)
        if not (r and r.get("pid")):
            self.set(4, False, "꺼 볼 켜진 시험 세션이 없다")
            return
        if not self.kill(self.a):
            self.set(4, False, "끊은 뒤 목록 · 관리자 명단에서 빠지지 않았다")
            return
        # 대화가 있는 세션은 끊은 채로 남아야 한다 (대화가 없으면 관리자가 다시 띄운다)
        respawned = self.wait(lambda: (self.row(self.a) or {}).get("pid"), 20)
        self.set(4, not respawned, "끊었는데 Claude 가 다시 띄웠다" if respawned else "")

    def step_resume(self) -> None:
        problems = []
        code, out = self.claude("--bg", "--resume", self.first or "")
        row = self.wait(lambda: (r := self.row(self.a)) and r.get("pid") and r, 40)
        if code != 0 or not row:
            problems.append(
                f"처음 대화 번호로 이어 열지 못했다: {out.splitlines()[0] if out else code}"
            )
        elif row.get("name") != self.name:
            problems.append(f"이어 연 뒤 이름이 {row.get('name')!r}")
        # 켜진 세션을 또 이어 열면 새 번호의 복사본이 생긴다 (연결 중 두 번 누르기를 막는 이유)
        _, out = self.claude("--bg", "--resume", self.first or "")
        m = _BG_ID.search(out)
        if not m or m.group(1) == self.a:
            problems.append(
                "켜진 세션을 또 이어 열었는데 복사본이 생기지 않았다 (막는 규칙을 다시 볼 것)"
            )
        elif copy := self.row(m.group(1)):
            from devdash.local import kill_tree

            if copy.get("pid"):
                kill_tree(copy["pid"])
            self.claude("rm", m.group(1))
        # 없는 대화 번호: 종료 코드 0 으로 끝나고 나중에 실패한다 (연결 확인이 상태를 보는 이유)
        code, out = self.claude("--bg", "--resume", str(uuid.uuid4()))
        m = _BG_ID.search(out)
        failed = self.wait(
            lambda: m and (r := self.row(m.group(1))) is not None and not r.get("pid"), 40, 3
        )
        self.fake_resume_failed = code == 0 and bool(failed)
        if code != 0:
            problems.append(f"없는 번호의 종료 코드가 {code}")
        elif not failed:
            problems.append("없는 번호로 이어 연 세션이 실패로 끝나지 않았다")
        self.set(3, not problems, "; ".join(problems))

    def step_clear(self) -> None:
        if not self.kill(self.a or ""):
            self.set(5, False, "시험 세션을 끄지 못했다")
            return
        self.claude("--bg", "--resume", self.first or "", "/clear")

        def state() -> dict:
            path = agents.JOBS / (self.a or "") / "state.json"
            return json.loads(path.read_text("utf-8")) if path.exists() else {}

        def settled() -> str | None:
            """/clear 뒤 대화 번호 — 관리자 기록과 목록이 같은 새 번호를 가리킬 때.
            깨어나는 중에는 목록의 대화 번호가 잠깐 다른 값으로 보여서 목록만 믿으면 틀린다."""
            new = state().get("resumeSessionId")
            row = self.row(self.a) or {}
            if new and new != self.first and row.get("pid") and row.get("sessionId") == new:
                return new
            return None

        new = self.wait(settled, 90, 3)
        problems = []
        if not new:
            got, row = state(), self.row(self.a) or {}
            self.set(
                5,
                False,
                f"/clear 뒤 대화 번호가 자리 잡지 않았다 (목록 {str(row.get('sessionId'))[:8]}, "
                f"관리자 기록 {str(got.get('resumeSessionId'))[:8]})",
            )
            return
        row = self.row(self.a) or {}
        if row.get("name") != self.name:
            problems.append(f"/clear 뒤 이름이 {row.get('name')!r}")
        kept = str(state().get("sessionId"))
        if kept != self.first:
            problems.append(f"state.json 의 sessionId 가 처음 번호가 아니다: {kept[:8]}")
        self.stop(self.a or "")
        if (self.row(self.a) or {}).get("sessionId") != self.first:
            problems.append("끈 뒤 --all 의 대화 번호가 처음 번호가 아니다")
        self.claude("--bg", "--resume", self.first or "")
        # 깨어나는 중에는 대화 번호가 잠깐 다른 값으로 보인다 — /clear 뒤 번호가 될 때까지 본다
        back = self.wait(
            lambda: (r := self.row(self.a)) and r.get("pid") and r.get("sessionId") == new, 60
        )
        if not back:
            now = self.row(self.a) or {}
            problems.append(
                f"처음 번호로 이어 열었는데 /clear 뒤 대화({new[:8]})로 돌아오지 않았다 "
                f"(지금 {str(now.get('sessionId'))[:8]}, 켜짐 {bool(now.get('pid'))})"
            )
        self.set(5, not problems, "; ".join(problems))

    def step_hooks(self) -> None:
        reason = check_hooks(self.captured())
        self.set(9, reason is None, reason or "")

    def step_delete(self) -> None:
        sid = self.a or ""
        flags = agents.respawn_flags(sid)
        conversation = agents.last_conversation(sid) or self.first or ""
        self.stop(sid)
        code, out = self.claude("rm", sid)
        problems = []
        if code != 0:
            self.set(15, False, f"claude rm 실패: {out[:80]}")
            return
        if self.row(sid):
            problems.append("rm 뒤에도 --all 에 남았다")
        if (agents.JOBS / sid).exists():
            problems.append("rm 뒤에도 jobs/<번호> 가 남았다")
        if not self.transcript_path(conversation).exists():
            problems.append("rm 이 대화 기록 파일까지 지웠다 (복원할 수 없다)")
        err = agents.restore(str(self.cwd), conversation, flags, self.name, remote=False)
        # 목록 번호는 이어 연 대화 번호의 앞 8자리다: /clear 한 적 없으면 같은 번호,
        # /clear 를 거쳤으면 /clear 뒤 대화 번호로 새로 생긴다 (대시보드 [복원]은 둘 다 찾는다)
        back_id = conversation[:8]
        row = self.wait(lambda: (r := self.row(back_id)) and r.get("pid") and r, 40)
        if err or not row:
            problems.append(f"복원(이어 열기)한 세션이 {back_id} 로 나타나지 않았다: {err or ''}")
        else:
            self.a = back_id
            back = agents.respawn_flags(back_id)
            if "--name" not in back:
                problems.append(f"복원 뒤 실행 옵션에 --name 이 없다: {back}")
            if row.get("name") != self.name:
                problems.append(f"복원 뒤 이름이 {row.get('name')!r}")
        self.set(15, not problems, "; ".join(problems))

    def step_retention(self) -> None:
        try:
            settings = json.loads((claude_dir() / "settings.json").read_text(encoding="utf-8"))
            days = settings.get("cleanupPeriodDays")
        except (OSError, ValueError, AttributeError):
            days = None
        readable = days is None or (isinstance(days, int) and days >= 1)
        note = f"cleanupPeriodDays {days}일" if days else "cleanupPeriodDays 없음 (기본 30일)"
        if not readable:
            self.set(16, False, f"cleanupPeriodDays 를 읽지 못했다: {days!r}")
        elif self.fake_resume_failed is False:
            self.set(16, False, "없는(정리된) 대화를 이어 열었는데 실패하지 않았다")
        else:
            self.set(16, True, note)

    def step_print(self) -> None:
        code, out = self.claude("-p", "--model", MODEL, "'확인'이라고만 답해.", timeout=120)
        self.set(
            13, code == 0 and bool(out), "" if code == 0 and out else f"코드 {code}: {out[:80]}"
        )

    def step_plugin(self) -> None:
        real = _fingerprint(claude_dir())
        tmp = Path(tempfile.mkdtemp(prefix="devdash-compat-claude-"))
        env = {**self.env, "CLAUDE_CONFIG_DIR": str(tmp)}
        plugin = "devdash@dev-dashboard"
        problems = []
        try:
            self.claude("plugin", "marketplace", "add", str(REPO), env=env)
            self.claude("plugin", "install", plugin, "--scope", "user", env=env)
            markets = self.json_list("plugin", "marketplace", "list", "--json", env=env)
            plugins = self.json_list("plugin", "list", "--json", env=env)
            if reason := check_plugin_lists(markets, plugins):
                problems.append(reason)
            elif not any(m.get("name") == "dev-dashboard" for m in markets) or not any(
                p.get("id") == plugin for p in plugins
            ):
                problems.append("마켓플레이스 등록 · 플러그인 설치가 목록에 보이지 않는다")
            self.claude("plugin", "uninstall", plugin, "--scope", "user", env=env)
            self.claude("plugin", "marketplace", "remove", "dev-dashboard", env=env)
            markets = self.json_list("plugin", "marketplace", "list", "--json", env=env) or []
            if any(m.get("name") == "dev-dashboard" for m in markets):
                problems.append("마켓플레이스를 지웠는데 남았다")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if _fingerprint(claude_dir()) != real:
            problems.append("시험용 설정 폴더로 돌렸는데 실제 Claude 설정 파일이 바뀌었다")
        self.set(10, not problems, "; ".join(problems))

    # --- 전체 ---

    def run(self) -> tuple[str | None, list[Item]]:
        before = {r["id"] for r in self.every() if r.get("id")}
        real = _fingerprint(claude_dir())
        version = None
        self.prepare()
        try:
            version = self.step_version()
            self.step_create()
            steps = [
                self.step_list,
                self.step_transcript,
                self.step_edits,
                self.step_jobs,
                self.step_name,
                self.step_kill,
                self.step_resume,
                self.step_clear,
                self.step_hooks,
                self.step_delete,
                self.step_retention,
                self.step_print,
                self.step_plugin,
            ]
            for step in steps:
                if self.a is None and step not in (
                    self.step_edits,
                    self.step_print,
                    self.step_plugin,
                ):
                    continue  # 시험 세션이 없으면 그 세션으로 하는 항목은 건너뛴다
                try:
                    step()
                except Exception as exc:  # 한 항목이 깨져도 나머지는 돈다
                    num = next(
                        (n for n, i in self.items.items() if i.ok is None and not i.reason), 0
                    )
                    self.say(f"  오류 ({step.__name__}): {type(exc).__name__}: {exc}")
                    if num:
                        self.set(num, False, f"{type(exc).__name__}: {exc}")
        finally:
            self.cleanup()
        for item in self.items.values():
            if item.ok is None and not item.reason:
                item.reason = "시험 세션을 띄우지 못해 하지 못함"
                item.ok = False
        after = {r["id"] for r in self.every() if r.get("id")}
        if after != before:
            self.say(
                f"  주의: 시험 뒤 세션 목록이 달라졌다 "
                f"(+{sorted(after - before)} -{sorted(before - after)})"
            )
        if _fingerprint(claude_dir()) != real:
            self.say("  주의: 시험 중에 실제 Claude 설정 파일이 바뀌었다")
        return version, [self.items[n] for n in sorted(self.items)]


def main(say: Callable[[str], None] = print) -> int:
    if (other := running()) is not None:
        say(f"다른 호환 시험이 돌고 있습니다 (PID {other['pid']}). 끝난 뒤 다시 하세요.")
        return 1
    runner = Runner(say)
    if err := runner.preflight():
        say(err)
        return 1
    # 돌고 있다는 표시 — 대시보드가 '시험하는 중' 을 보이고, 자동 시험과 겹치지 않게
    running_path().parent.mkdir(parents=True, exist_ok=True)
    running_path().write_text(
        json.dumps({"pid": os.getpid(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8"
    )
    try:
        say(f"Claude Code 호환 시험 — 시험 폴더 {runner.cwd} (몇 분 걸린다)\n")
        version, items = runner.run()
        data = save_results(version, items)
    finally:
        running_path().unlink(missing_ok=True)
    failed = [i for i in items if i.ok is False]
    say("")
    if failed:
        say(
            f"Claude Code {version}: {len(failed)}개 항목 실패 — 위 이유를 확인하세요. "
            f"결과: {results_path()}"
        )
    else:
        say(f"Claude Code {version}: 모두 통과. 대시보드의 '시험하지 않은 버전' 표시가 사라집니다.")
    return 0 if data["passed"] else 1
