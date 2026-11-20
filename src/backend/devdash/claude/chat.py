"""세션의 대화 (프로젝트 탭 [세션] 보기).

Claude Code 는 대화를 ~/.claude/projects/<폴더>/<세션 번호>.jsonl 에 한 줄씩 쌓는다.
한 줄에는 사람이 친 질문 · Claude 의 답 · 생각 · 도구 사용(파일 읽기 · 명령 실행 …)과
그 결과가 섞여 있다.

화면에는 **질문과 답만** 보인다. 도구 사용은 숨기고 답마다 횟수만 센다 — 한 차례에 수십 번 쓰기도
해서 다 보이면 대화가 묻히고, 자세한 것은 Claude Code 앱에서 본다.

기록 파일은 수십 MB 다 (이 저장소의 한 세션이 54MB). 대부분 도구 결과라서, 끝에서부터 거꾸로 읽어
필요한 질문 수만큼만 본다.
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from pathlib import Path

from devdash.home import claude_dir

PROJECTS = claude_dir() / "projects"

CHUNK = 1024 * 1024
"""끝에서부터 한 번에 읽는 크기 (바이트)."""

MAX_LIMIT = 200
"""한 번에 가져오는 질문 수 상한 ([더 보기]를 많이 눌러도 응답이 끝없이 커지지 않게)."""

SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

NOT_ASKED = (
    "<task-notification",
    "<system-reminder",
    "<local-command-caveat",
    "<local-command-stdout",
    "<local-command-stderr",
    "<bash-stdout",
    "<bash-stderr",
    "Another Claude session sent a message",
)
"""user 줄이지만 사람이 친 것이 아니라 Claude Code 가 끼워 넣은 알림 · 출력."""


def transcript(session_id: str, root: Path | None = None) -> Path | None:
    """세션 번호의 대화 기록 파일. 작업용 복사본에서 일한 세션은 다른 폴더에 있어
    모든 폴더에서 찾는다. 번호 모양이 아니면 찾지 않는다 (경로를 만들어 넣지 못하게)."""
    if not SESSION_ID.fullmatch(session_id):  # match 의 $ 는 끝의 줄바꿈을 봐준다
        return None
    root = root or PROJECTS  # 기본값을 부를 때 읽는다 (정의할 때 묶으면 바꿔 끼울 수 없다)
    hits = sorted(root.glob(f"*/{session_id}.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def _lines_backward(path: Path):
    """파일의 줄을 끝에서부터 하나씩 (바이트). 수십 MB 를 통째로 읽지 않으려고."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        rest = b""
        while pos > 0:
            step = min(CHUNK, pos)
            pos -= step
            f.seek(pos)
            block = f.read(step) + rest
            lines = block.split(b"\n")
            rest = lines[0]  # 앞부분이 잘린 줄일 수 있어 다음 덩어리와 잇는다
            for line in reversed(lines[1:]):
                if line.strip():
                    yield line
        if rest.strip():
            yield rest


def _question(entry: dict) -> str | None:
    """사람이 친 질문이면 그 글, 아니면 None."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    if entry.get("isCompactSummary"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                parts.append("[이미지]")
        text = "\n".join(parts)
    else:
        return None
    text = text.strip()
    if not text or text.startswith(NOT_ASKED):
        return None
    if m := re.search(r"<command-name>(.*?)</command-name>", text, re.S):
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return f"{m.group(1).strip()} {args.group(1).strip() if args else ''}".strip()
    if m := re.fullmatch(r"<bash-input>(.*?)</bash-input>", text, re.S):
        return f"! {m.group(1).strip()}"
    return text


def chat(path: Path, limit: int = 10) -> dict:
    """최근 질문 limit 개와 그 답들 (시간 순). 답의 글은 이어 붙이고 도구 사용은 이름별로 센다.

    {"messages": [{"role": "user"|"assistant", "text", "at", "prompt", "tools"?}],
     "more": 더 이전 질문이 있나}

    prompt 는 그 차례(질문 하나와 그 답)의 번호 — 훅이 남긴 변경 기록 · 스냅샷의 prompt 와 같아
    작업 상자를 붙일 때 쓴다 (records.chat_work).
    """
    limit = max(1, min(limit, MAX_LIMIT))
    out: list[dict] = []  # 거꾸로 모은다
    answer: dict | None = None  # 지금 모으는 답 (거꾸로 읽으므로 질문보다 먼저 만난다)
    asked = 0
    more = False

    def flush(prompt: str | None = None) -> None:
        nonlocal answer
        if answer and (answer["parts"] or answer["tools"]):
            out.append(
                {
                    "role": "assistant",
                    "text": "\n\n".join(reversed(answer["parts"])).strip(),
                    "at": answer["at"],
                    "prompt": prompt,
                    "tools": dict(answer["tools"]),
                }
            )
        answer = None

    for raw in _lines_backward(path):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if asked >= limit:
            # 이미 다 모았다. 더 이전에 질문이 하나라도 있으면 [더 보기]를 보인다
            if _question(entry) is not None:
                more = True
                break
            continue
        if entry.get("type") == "assistant" and not entry.get("isSidechain"):
            if answer is None:
                answer = {"parts": [], "tools": Counter(), "at": entry.get("timestamp")}
            for b in (entry.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    answer["parts"].append(b["text"].strip())
                elif b.get("type") == "tool_use":
                    answer["tools"][b.get("name", "?")] += 1
            continue
        question = _question(entry)
        if question is None:
            continue
        prompt = entry.get("promptId")
        flush(prompt)  # 거꾸로 읽으므로 이 질문이 방금 모은 답의 질문이다
        out.append(
            {"role": "user", "text": question, "at": entry.get("timestamp"), "prompt": prompt}
        )
        asked += 1
    if asked < limit:
        flush()  # 파일 맨 앞까지 왔으면 첫 질문 앞의 답(있다면)도 넣는다
    return {"messages": list(reversed(out)), "more": more}


REPLY_WINDOW = 20
"""세션 목록이 보는 최근 질문 수. 새 답 수는 이 안에서만 센다 (그보다 많으면 '20+')."""


def summary(path: Path) -> dict:
    """세션 목록 한 줄에 필요한 것: 최근 답이 끝난 시각들과 마지막 대화 시각.

    {"replies": [시각, …] (오래된 것부터), "lastAt": 마지막 질문이나 답의 시각}
    """
    messages = chat(path, REPLY_WINDOW)["messages"]
    return {
        "replies": [m["at"] for m in messages if m["role"] == "assistant" and m["at"]],
        "lastAt": messages[-1]["at"] if messages else None,
    }


# --- 컨텍스트 사용량과 압축 횟수 ---

COMPACT_MARK = b"compact_boundary"
"""Claude Code 가 대화를 압축할 때마다 남기는 기록 줄(system · compact_boundary)의 낱말.
이 낱말이 든 줄만 해석해서 정말 압축 기록인지 본다 — 메시지 글 속의 같은 낱말은 세지 않는다."""

_scans: dict[str, dict] = {}  # 경로 → {offset, count, first, base, pending} (_scan)
_compactions_lock = threading.Lock()


def _input_tokens(usage: dict) -> int:
    """답 하나를 만들 때 모델에 들어간 입력 = 새 입력 + 캐시에 쓴 것 + 캐시에서 읽은 것."""
    return (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
    )


def _scan(path: Path) -> dict:
    """기록을 앞에서부터, 지난번에 읽은 곳 뒤로 덧붙은 부분만 읽으며 센다 (기록은 수십 MB 이고
    뒤에 덧붙기만 한다. 줄어들었으면 처음부터 다시).

    - count: 압축 횟수
    - base: 기본분 — 시스템 프롬프트 · 도구 · 메모리처럼 대화와 상관없이 늘 들어가는 토큰.
      압축 기록의 postTokens 는 대화 요약만 세서 압축 직후 실제 입력보다 5만~6.5만 적었다 (이 세션
      기록으로 확인). 그래서 '압축 뒤 첫 답의 입력 - 그 압축의 postTokens' 를 기억해 둔다.
      아직 그런 짝이 없으면 None 이고, 대신 first(세션 첫 답의 입력)를 쓴다
    """
    key = str(path)
    with _compactions_lock:
        state = dict(_scans.get(key) or {})
    size = path.stat().st_size
    if not state or size < state["offset"]:
        state = {"offset": 0, "count": 0, "first": None, "base": None, "pending": None}
    with open(path, "rb") as f:
        f.seek(state["offset"])
        chunk = f.read(size - state["offset"])
    end = chunk.rfind(b"\n") + 1  # 아직 쓰는 중인 마지막 줄은 다음에 읽는다
    for line in chunk[:end].split(b"\n"):
        compact = COMPACT_MARK in line
        # 답 줄은 '첫 답'이나 '압축 뒤 첫 답'을 기다릴 때만 해석한다 (나머지는 건너뛴다)
        want_answer = state["first"] is None or state["pending"] is not None
        if not compact and not (want_answer and b'"usage"' in line and b'"assistant"' in line):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("isSidechain"):
            continue
        if compact and entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
            state["count"] += 1
            post = (entry.get("compactMetadata") or {}).get("postTokens")
            state["pending"] = post if isinstance(post, int) else None
        elif entry.get("type") == "assistant":
            usage = (entry.get("message") or {}).get("usage")
            if not usage:
                continue
            total = _input_tokens(usage)
            if state["first"] is None:
                state["first"] = total
            if state["pending"] is not None:
                state["base"] = total - state["pending"]
                state["pending"] = None
    state["offset"] += end
    with _compactions_lock:
        _scans[key] = state
    return state


def compactions(path: Path) -> int:
    """이 대화가 압축된 횟수."""
    return _scan(path)["count"]


_context_paths: dict[str, Path] = {}  # 세션 번호 → 기록 파일
_context_cache: dict[
    str, tuple[tuple[int, int], dict]
] = {}  # 기록 파일 → ((크기, 바뀐 시각), 결과)


def context(session_id: str) -> dict:
    """세션 목록 한 줄에 붙는 {"tokens": 컨텍스트 토큰 수 | None, "compactions": 압축 횟수}.
    %는 화면이 100만 토큰 기준으로 계산한다. 기록 파일이 없으면 None · 0.

    세션 칸은 3초마다 세션 13개를 묻는다. 기록 파일 위치와, 파일이 그대로일 때의 결과를 기억해 두어
    바뀌지 않은 세션은 파일 크기만 보고 넘어간다 (안 그러면 요청마다 0.2초~수 초가 더 들었다)."""
    empty = {"tokens": None, "compactions": 0}
    with _compactions_lock:
        path = _context_paths.get(session_id)
    try:
        if path is None or not path.exists():
            path = transcript(session_id)
            if path is None:
                return empty
            with _compactions_lock:
                _context_paths[session_id] = path
        st = path.stat()
        stamp = (st.st_size, st.st_mtime_ns)
        with _compactions_lock:
            hit = _context_cache.get(str(path))
        if hit and hit[0] == stamp:
            return hit[1]
        value = {"tokens": context_tokens(path), "compactions": compactions(path)}
    except OSError:
        return empty
    with _compactions_lock:
        _context_cache[str(path)] = (stamp, value)
    return value


TITLE_MARK = b'"custom-title"'

_title_cache: dict[str, tuple[tuple[int, int], str | None]] = {}  # 기록 파일 → (크기 · 시각, 이름)


def title(session_id: str) -> str | None:
    """대화 기록에 남은 마지막 세션 이름(`/rename` · `--name` 이 남기는 custom-title). 없으면 None.

    Claude 관리자가 세션을 스스로 다시 띄울 때는 실행 옵션의 --name 만 보고 이 이름을 읽지 않아,
    --name 없이 이름을 붙인 세션은 `claude agents` 이름이 목록 번호가 된다.
    그런 세션을 알아보게 할 때 쓴다.
    끝에서부터 읽고, 파일이 그대로면 기억해 둔 값을 준다."""
    with _compactions_lock:
        path = _context_paths.get(session_id)
    try:
        if path is None or not path.exists():
            path = transcript(session_id)
            if path is None:
                return None
        st = path.stat()
        stamp = (st.st_size, st.st_mtime_ns)
        with _compactions_lock:
            hit = _title_cache.get(str(path))
        if hit and hit[0] == stamp:
            return hit[1]
        value = None
        for raw in _lines_backward(path):
            if TITLE_MARK not in raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if entry.get("type") == "custom-title" and isinstance(entry.get("customTitle"), str):
                value = entry["customTitle"].strip() or None
                break
    except OSError:
        return None
    with _compactions_lock:
        _title_cache[str(path)] = (stamp, value)
    return value


def context_tokens(path: Path) -> int | None:
    """지금 컨텍스트에 든 토큰 수 = 마지막 답을 만들 때 모델에 들어간 입력(새 입력 + 캐시).
    서브에이전트의 답은 빼고 본다. 아직 답이 없으면 None.

    답보다 압축 기록이 더 최근이면 '압축 뒤 대화 요약(postTokens) + 기본분(_scan)'으로 어림한다 —
    안 그러면 압축 직후에도 다음 답이 나올 때까지 압축 전 %가 남았다 (/clear 시험에서 확인).
    이 세션의 압축 4번으로 맞춰 보면 어림과 실제의 차이는 100만 기준 ±1.5%p 안이다.
    어림은 압축 뒤 다음 답이 나오기 전까지만 쓰이고, 답이 나오면 그 답의 실제 입력으로 바뀐다."""
    for raw in _lines_backward(path):
        is_compact = COMPACT_MARK in raw
        if not is_compact and (b'"assistant"' not in raw or b'"usage"' not in raw):
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if entry.get("isSidechain"):
            continue
        if (
            is_compact
            and entry.get("type") == "system"
            and entry.get("subtype") == "compact_boundary"
        ):
            post = (entry.get("compactMetadata") or {}).get("postTokens")
            if isinstance(post, int):
                scan = _scan(path)
                base = scan["base"] if scan["base"] is not None else (scan["first"] or 0)
                return post + base
            continue
        if entry.get("type") != "assistant":
            continue
        usage = (entry.get("message") or {}).get("usage") or {}
        if usage:
            return _input_tokens(usage)
    return None
