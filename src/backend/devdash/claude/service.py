"""세션 칸의 동작 — 목록 · 연결 · 해제 · 포그라운드 · 삭제 · 복원 · 새로 만들기 · 그룹.

세션은 등록하지 않는다. `claude agents --all` 에 있는 백그라운드 세션이 모두 보이고, 사람은
그룹 · 순서만 정한다 (registry). 필요 없는 세션은 [삭제] — Claude 목록에서도 지운다(claude rm).
대화형(터미널) 세션은 목록 번호가 없어 보이기만 한다.

세션은 대기 중에도 하나에 ~250MB 를 먹고, 메모리가 모자라면 Claude 관리자가 쉬는 세션을
끈다. 그래서 쓸 것만 켜 두도록 하나씩 켜고 끄는 화면을 둔다.

버튼 동작의 응답은 셋 중 하나다.
  None              성공
  str               실패 이유 (화면 안내 상자에 띄운다)
  {"confirm": 말}   한 번 물어봐야 하는 경우 (확인하면 force 로 다시 부른다)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from devdash.claude import agents, chat, registry
from devdash.home import sessions_path
from devdash.local import kill_tree, open_terminal

OP_TIMEOUT = 40
"""초. 연결/해제가 이 안에 반영되지 않으면 '바뀌는 중' 표시를 푼다."""

FAIL_SHOW = 60
"""초. 실패 이유를 화면 갱신에 실어 보내는 시간. 화면은 같은 실패를 한 번만 띄운다."""

LIST_CACHE_SECONDS = 2
"""claude agents 는 1~2초 걸린다. 화면이 여러 번 물어도 이 안이면 한 번만 부른다."""

Result = str | dict | None

UNGROUPED = "분류 안 됨"

NO_TALK_STOP = (
    "아직 대화가 없는 세션은 꺼도 Claude 가 곧 다시 켭니다. "
    "한 번 대화한 뒤 해제하거나, 필요 없으면 [삭제]하세요"
)
NO_TALK_START = "아직 대화가 없는 세션이라 Claude 가 곧 다시 켭니다. 잠시 뒤 켜진 것으로 보입니다"


logger = logging.getLogger(__name__)


def lost_name(row: dict, conversation: str) -> str | None:
    """Claude 목록에서 이름을 잃은 세션의 원래 이름 — 목록 이름이 목록 번호로 나오는데 대화 기록에는
    이름(custom-title)이 남아 있으면 그 이름. 아니면 None.

    Claude 관리자가 스스로 다시 띄울 때는 실행 옵션의 --name 만 봐서, --name 없이 붙인 이름
    (대화형일 때 /rename 등)은 목록에서 번호로 바뀐다. 대화 기록의 이름은 Claude 쪽 이름이라
    대시보드만의 별명이 아니다."""
    if has_name(row) or not conversation:
        return None
    return chat.title(conversation)


def generations(sessions: dict[str, dict]) -> dict[str, list[str]]:
    """같은 세션의 옛 세대 묶기: {최근 세대 목록 번호: [옛 세대 목록 번호 …(새 것부터)]}.

    옛 대시보드가 /clear 를 따라가며 이어 열 때, 같은 대화가 목록 번호를 바꿔 가며 여러 줄로 남았다.
    어떤 줄의 마지막 대화(관리자 기록의 resumeSessionId)가 다른 줄의 처음 대화(목록의 sessionId)면
    그 줄은 다른 줄의 옛 세대다. 사슬을 끝까지 따라가 가장 새 줄을 최근 세대로 본다.
    Claude 목록에서는 지우지 않는다 — claude rm 은 그 줄이 주인인 작업용 복사본까지 지운다."""
    first = {sid: r.get("resumeId") or r.get("sessionId") for sid, r in sessions.items()}
    owner = {conv: sid for sid, conv in first.items() if conv}
    newer: dict[str, str] = {}
    for sid in sessions:
        last = agents.last_conversation(sid)
        nxt = owner.get(last) if last and last != first[sid] else None
        if nxt and nxt != sid:
            newer[sid] = nxt
    out: dict[str, list[str]] = {}
    for sid in newer:
        head, seen = sid, {sid}
        while head in newer and newer[head] not in seen:  # 고리면 멈춘다
            head = newer[head]
            seen.add(head)
        if head in newer:  # 고리 — 어느 쪽도 최근 세대로 정할 수 없어 숨기지 않는다
            continue
        out.setdefault(head, []).append(sid)
    for head, olds in out.items():
        # 새 것부터: 최근 세대에 가까운 순서
        def distance(sid: str, head: str = head) -> int:
            n, cur = 0, sid
            while cur != head:
                cur, n = newer[cur], n + 1
            return n

        olds.sort(key=distance)
    return out


def hidden_older(sessions: dict[str, dict]) -> dict[str, list[str]]:
    """화면에서 숨길 옛 세대: generations 중 꺼진(pid 없는) 줄만. 켜진 옛 세대는 보인다 —
    앱 사이드바에서 깨우면 다시 켜질 수 있는데, 숨기면 돌고 있는 세션을 보지도 끄지도 못한다.
    [삭제]가 함께 지우는 것도 이 줄들뿐이다 (보이는 줄은 따로 지운다)."""
    out = {}
    for head, olds in generations(sessions).items():
        off = [o for o in olds if not sessions[o].get("pid")]
        if off:
            out[head] = off
    return out


def has_name(row: dict) -> bool:
    """Claude 쪽 이름이 있는가. 켜진 세션은 이름이 없으면 목록 번호를 이름 자리에 넣어 준다."""
    return bool(row.get("name")) and row.get("name") != row.get("id")


def display_name(row: dict) -> str:
    """Claude 쪽 이름. 없으면 '폴더 이름 · 번호 앞 8자리' — 이름 없는 세션도 가려 볼 수 있게."""
    if has_name(row):
        return row["name"]
    return f"{agents.folder_label(row.get('cwd') or '')} · {row['id'][:8]}"


class SessionService:
    def __init__(self, path: Path | None = None):
        self.path = path or sessions_path()
        self._ops: dict[str, tuple[str, float]] = {}  # 번호 → (start|stop|delete|restore, 시각)
        self._failures: dict[str, tuple[str, float]] = {}  # 번호 → (이유, 시각)
        self._lists: tuple[float, list, list] = (0.0, [], [])
        self._lock = threading.Lock()

    def _background(self, fn, *args) -> None:
        """오래 걸리는 일(이어 띄우기 · 명단 대기 · 지우기)은 뒤에서 돌리고 응답은 바로 준다.
        시험은 이 메서드만 바꿔 끼워 실제로 띄우지 않는다."""
        threading.Thread(target=self._logged, args=(fn, *args), daemon=True).start()

    @staticmethod
    def _logged(fn, *args) -> None:
        """뒤에서 돈 일의 예외를 로그에 남긴다 — 남기지 않으면 아무 흔적 없이 사라진다."""
        try:
            fn(*args)
        except Exception:
            logger.exception("뒤에서 돌던 일이 실패했다: %s", getattr(fn, "__name__", fn))

    # --- 목록 ---

    def _cached_lists(self) -> tuple[list, list]:
        with self._lock:
            if time.time() - self._lists[0] > LIST_CACHE_SECONDS:
                self._lists = (time.time(), agents.live_sessions(), agents.all_sessions())
            return self._lists[1], self._lists[2]

    def _drop_cache(self) -> None:
        with self._lock:
            self._lists = (0.0, [], [])

    def collect(self) -> tuple[dict[str, dict], list[dict]]:
        """(목록 번호 → 백그라운드 세션, 대화형 세션 목록).

        `--all` 이 바탕이고, 켜진 목록(`--json`)의 값(pid · 상태 · 지금 대화 번호)이 덮는다.
        `--all` 의 sessionId 는 처음 대화 번호라 이어 열 때 쓰도록 resumeId 로 따로 둔다."""
        live, every = self._cached_lists()
        sessions: dict[str, dict] = {}
        for r in every:
            if r.get("id") and r.get("kind") != "interactive":
                sessions[r["id"]] = {**r, "resumeId": r.get("sessionId")}
        interactive = []
        for r in live:
            if r.get("kind") == "interactive" or not r.get("id"):
                interactive.append(r)
                continue
            base = sessions.setdefault(r["id"], {"resumeId": r.get("sessionId")})
            base.update(r)
        return sessions, interactive

    def entry(self, session_id: str) -> dict | None:
        return self.collect()[0].get(session_id)

    @staticmethod
    def conversation(row: dict) -> str | None:
        """대화 · 토큰을 읽을 대화 번호.
        켜졌으면 지금 번호, 꺼졌으면 관리자가 기억하는 마지막 번호."""
        if row.get("pid"):
            return row.get("sessionId")
        return agents.last_conversation(row.get("id", "")) or row.get("sessionId")

    def conversation_of(self, key: str) -> str | None:
        """목록 번호(백그라운드) 또는 대화 번호(대화형)로 지금 보이는 세션의 대화 번호를 찾는다.
        목록에 보이는 세션만 — 아무 대화 기록이나 읽어 주는 통로가 되지 않게."""
        sessions, interactive = self.collect()
        if key in sessions:
            return self.conversation(sessions[key])
        hit = next((r for r in interactive if r.get("sessionId") == key), None)
        return hit.get("sessionId") if hit else None

    def _state(self, row: dict) -> str:
        sid = row["id"]
        state = "on" if row.get("pid") else "off"
        op = self._ops.get(sid)
        if op:
            # 해제 · 삭제는 '꺼짐'으로 보여도 끝난 게 아니다 — 관리자 명단에서 빠진 것을 확인하고
            # 직접 표시를 푼다. 그 전에 풀면 연결이 눌려 복사본이 생긴다.
            done = op[0] == "start" and state == "on"
            if done or time.time() - op[1] > OP_TIMEOUT:
                self._ops.pop(sid, None)
            else:
                state = {"start": "starting", "stop": "stopping", "delete": "deleting"}[op[0]]
        return state

    def _row(self, row: dict, data: dict) -> dict:
        sid = row["id"]
        conversation = self.conversation(row) or ""
        lost = lost_name(row, conversation)
        out = {
            "id": sid,
            "name": lost or display_name(row),
            "named": has_name(row) or bool(lost),
            # 목록에서 이름을 잃어 대화 기록의 이름을 보인다 (화면은 기울이고 /rename 을 안내)
            "lostName": bool(lost),
            "group": registry.group_of(data, sid),
            "order": registry.order_of(data, sid),
            "project": registry.project_of(data, sid),
            "cwd": row.get("cwd") or "",
            "state": self._state(row),
            # 대화 · 토큰을 읽는 번호. 프로젝트 탭이 새 답 수를 셀 때도 쓴다
            "conversation": conversation,
            # 컨텍스트 토큰 수 · 압축 횟수. 꺼진 세션도 마지막 상태를 보인다
            **chat.context(conversation),
        }
        if worktree := agents.worktree_of(sid):
            # [삭제] 확인 창이 경고한다 — claude rm 은 변경이 없으면 이 폴더까지 지운다
            out["worktree"] = worktree
        if row.get("pid"):
            # 켜진 세션이 지금 무엇을 하나: working(답을 만드는 중) · blocked(사람의 확인이나
            # 답을 기다림) · done(쉬는 중). 프로젝트 탭 [세션] 목록도 보인다
            out["activity"] = row.get("state")
        fail = self._failures.get(sid)
        if fail and time.time() - fail[1] <= FAIL_SHOW:
            out["error"], out["errorAt"] = fail
        return out

    @staticmethod
    def _worktree_users(rows: list[dict]) -> None:
        """같은 작업용 복사본을 주인으로 가진 다른 줄의 이름 — 옛 세대 줄이 지금 세션의
        복사본 주인으로 남는 일이 있다. [삭제] 확인 창이 함께 보인다."""
        by_path: dict[str, list[dict]] = {}
        for r in rows:
            if r.get("worktree"):
                by_path.setdefault(os.path.normcase(r["worktree"]), []).append(r)
        for same in by_path.values():
            for r in same:
                others = [o["name"] for o in same if o is not r]
                if others:
                    r["worktreeShared"] = others

    def rows(self) -> list[dict]:
        """백그라운드 세션 줄 — 그룹 순서, 그룹 안 순서, 이름 순."""
        data = registry.load(self.path)
        sessions, _ = self.collect()
        # 같은 세션의 꺼진 옛 세대는 숨기고 최근 세대 줄만 (그룹 · 순서도 최근 세대 줄의 것)
        older = hidden_older(sessions)
        hidden = {sid for olds in older.values() for sid in olds}
        rows = []
        for sid, r in sessions.items():
            if sid in hidden:
                continue
            row = self._row(r, data)
            if sid in older:
                row["olderGenerations"] = older[sid]
            rows.append(row)
        rank = {g: i for i, g in enumerate(registry.group_names(data))}

        def key(r: dict):
            group = rank.get(r["group"], len(rank)) if r["group"] else len(rank)
            order = r["order"] if r["order"] is not None else 1 << 30
            return (group, order, r["name"].casefold())

        self._worktree_users(rows)
        return sorted(rows, key=key)

    def view(self) -> dict:
        """세션 칸 전체: 그룹 · 세션 · 대화형 · 삭제한 세션."""
        data = registry.load(self.path)
        registry.prune_deleted(data)
        _, interactive = self.collect()
        return {
            "groups": data["groups"],
            "sessions": self.rows(),
            "interactive": [
                {
                    "sessionId": r.get("sessionId") or "",
                    "name": r.get("name") or agents.folder_label(r.get("cwd") or ""),
                    "cwd": r.get("cwd") or "",
                    "state": "terminal",
                    "activity": r.get("state"),
                    **chat.context(r.get("sessionId") or ""),
                }
                for r in interactive
            ],
            "deleted": [
                {
                    **d,
                    "name": d.get("name")
                    or f"{agents.folder_label(d.get('cwd') or '')} · {d['id'][:8]}",
                    "restorable": chat.transcript(d.get("sessionId") or "") is not None,
                    "restoring": self._in_progress(d["id"]),
                }
                for d in sorted(data["deleted"], key=lambda d: -float(d.get("deletedAt") or 0))
            ],
            "failures": [
                {"error": msg, "errorAt": at}
                for sid, (msg, at) in self._failures.items()
                if sid.startswith("deleted:") and time.time() - at <= FAIL_SHOW
            ],
        }

    def _in_progress(self, key: str) -> bool:
        """띄운 세션이 목록에 나타나기까지 몇 초 걸린다. 그 사이 또 누르면 resume 이 두 번
        돌아 복사본이 생기므로, 진행 중이면 같은 세션의 요청을 받지 않는다."""
        op = self._ops.get(key)
        return bool(op) and time.time() - op[1] <= OP_TIMEOUT

    def _fail(self, key: str, message: str) -> None:
        self._ops.pop(key, None)
        self._failures[key] = (message, time.time())

    # --- 연결 ---

    @staticmethod
    def _wait_released(row: dict) -> bool:
        """막 해제한 세션은 관리자 명단에서 빠질 때까지 기다린다. 끝내 안 빠지면 False."""
        return all(
            agents.wait_roster_clear(c) for c in {row.get("sessionId"), row.get("resumeId")} if c
        )

    def _resume(self, row: dict) -> None:
        sid = row["id"]
        self._failures.pop(sid, None)
        # 끝내 명단에서 안 빠지면 띄우지 않는다 — 띄우면 복사본이 생긴다
        if not self._wait_released(row):
            err = "아직 정리 중이라 연결하지 못했습니다. 잠시 뒤 다시 눌러 주세요"
        else:
            try:
                err = agents.resume(row["cwd"], row["resumeId"]) or agents.confirm_started(sid)
            except Exception as ex:
                err = f"연결 중 오류: {ex!r}"
        if err:
            # '연결 중'에 묶어 두지 않고 바로 다시 누를 수 있게 풀고, 이유를 화면에 넘긴다
            self._fail(sid, f"{display_name(row)} 연결 실패 — {err}")

    def _can_resume(self, row: dict) -> str | None:
        if not row.get("resumeId"):
            return "이어 열 대화 번호를 찾지 못했습니다"
        if not os.path.isdir(row.get("cwd") or ""):
            return f"폴더가 없습니다: {row.get('cwd')}"
        return None

    @staticmethod
    def _respawns(row: dict) -> bool:
        """대화가 한 번도 없는 세션인가 — 관리자 기록은 '일하는 중'인데 실제로는 쉬는 중.
        끊으면 Claude 가 15초 안에 다시 띄우고, 꺼져 보이는 사이 이어 열면 복사본이 생긴다
        (agents.job_state). 진짜로 일하는 중인 세션은 해제를 따로 거절한다."""
        return agents.job_state(row["id"]) == "working" and row.get("state") != "working"

    def start(self, row: dict) -> Result:
        if self._in_progress(row["id"]):
            return "이미 처리 중입니다"
        if row.get("pid"):
            return "이미 켜져 있습니다"
        if self._respawns(row):
            return NO_TALK_START
        if err := self._can_resume(row):
            return err
        self._ops[row["id"]] = ("start", time.time())
        self._background(self._resume, row)
        self._drop_cache()
        return None

    # --- 해제 ---

    def _check_stop(self, row: dict, force: bool, verb: str) -> Result:
        """끌 수 있는지. 답을 만드는 중이면 거절, 백그라운드 작업이 돌면 한 번 묻는다."""
        # 프로세스를 끊는 방식이라 하던 일이 중간에 멈춘다
        if row.get("state") == "working":
            return f"작업 중입니다. 끝난 뒤 {verb}해 주세요"
        # 대화는 끝났는데 status 가 busy 면 세션이 띄운 백그라운드 작업(서버 등)이 도는 것이다
        if row.get("status") == "busy" and not force:
            n = agents.bg_task_count(row["id"])
            count = f" {n}개" if n else ""
            return {
                "confirm": f"{display_name(row)} 세션에서 서버 같은 백그라운드 작업이{count} "
                f"실행 중입니다.\n{verb}하면 이 작업도 함께 꺼집니다. {verb}할까요?"
            }
        return None

    def stop(self, row: dict, force: bool = False) -> Result:
        if self._in_progress(row["id"]):
            return "이미 처리 중입니다"
        if not row.get("pid"):
            return "이미 꺼져 있습니다"
        if self._respawns(row):
            return NO_TALK_STOP
        if answer := self._check_stop(row, force, "해제"):
            return answer
        self._ops[row["id"]] = ("stop", time.time())
        # claude stop 대신 프로세스를 끊는다. stop 은 세션을 '사용자가 끔'으로 기록해 Claude 앱
        # 목록에서 사라지게 하고, 끊으면 재부팅 때처럼 '연결 끊김'으로 남는다.
        kill_tree(row["pid"])

        # 관리자 명단에서 빠질 때(약 5초)까지 '해제 중'을 유지한 뒤 푼다
        def finish() -> None:
            agents.wait_roster_clear(row.get("sessionId") or "", limit=OP_TIMEOUT)
            self._ops.pop(row["id"], None)

        self._background(finish)
        self._drop_cache()
        return None

    # --- 포그라운드 ---

    def foreground(self, row: dict) -> Result:
        """터미널 창에서 연다. 꺼진 세션은 이어 열고(창을 닫으면 꺼짐), 켜진 세션은 붙기만
        한다(창을 닫아도 계속 돎). 같은 대화를 포그라운드로 또 열면 복사본이 생기기 때문이다."""
        if self._in_progress(row["id"]):
            return "이미 처리 중입니다"
        if row.get("pid"):
            args = ["claude", "attach", row["id"]]
        else:
            if err := self._can_resume(row):
                return err
            if self._respawns(row):
                return NO_TALK_START
            if agents.in_roster(row["resumeId"]):
                return "아직 정리 중입니다. 잠시 뒤 다시 눌러 주세요"
            args = ["claude", "--resume", row["resumeId"]]
            # 켜질 때까지 '연결 중'으로 둔다 — 그 사이 또 누르면 두 번 열린다
            self._ops[row["id"]] = ("start", time.time())
        open_terminal(row["cwd"], display_name(row), args)
        self._drop_cache()
        return None

    # --- 삭제 · 복원 ---

    def delete(self, row: dict, force: bool = False) -> Result:
        """Claude 목록에서 지운다 (claude rm). 켜져 있으면 먼저 끈다. 대화 기록 파일은 남고,
        복원 정보를 registry.KEEP_DELETED 동안 둔다."""
        if self._in_progress(row["id"]):
            return "이미 처리 중입니다"
        if row.get("pid") and (answer := self._check_stop(row, force, "삭제")):
            return answer
        self._ops[row["id"]] = ("delete", time.time())
        self._failures.pop(row["id"], None)
        # 숨겨 둔 옛 세대 줄도 함께 지운다 (최근 세대를 지운 뒤)
        olds = hidden_older(self.collect()[0]).get(row["id"], [])
        self._background(self._delete, {**row, "olderGenerations": olds})
        self._drop_cache()
        return None

    def _delete(self, row: dict) -> None:
        sid = row["id"]
        # 지우면 관리자 기록(jobs/<번호>)도 사라지므로 마지막 대화 번호를 먼저 읽어 둔다
        conversation = self.conversation(row)
        flags = agents.respawn_flags(sid)
        if row.get("pid"):
            kill_tree(row["pid"])
            agents.wait_roster_clear(row.get("sessionId") or "", limit=OP_TIMEOUT)
        # 복원 정보를 먼저 적는다 — 지운 뒤에 적다가 실패하면 목록에서는 빠졌는데 복원할 길이 없었다
        with registry.editing(self.path) as data:
            data["deleted"] = [d for d in data["deleted"] if d["id"] != sid]
            data["deleted"].append(
                {
                    "id": sid,
                    # 이름을 잃은 세션은 목록 이름이 번호다 — 번호가 아니라 대화 기록의 이름을 적어
                    # 복원 때 --name 으로 되살린다
                    "name": (row["name"] if has_name(row) else lost_name(row, conversation or ""))
                    or "",
                    "flags": flags,
                    "group": registry.group_of(data, sid),
                    "cwd": row.get("cwd") or "",
                    "sessionId": conversation or "",
                    "deletedAt": time.time(),
                }
            )
        try:
            err = agents.remove(sid)
        except Exception as exc:  # claude 를 못 부름 · 시간 초과
            err = str(exc) or type(exc).__name__
        self._drop_cache()
        listed, _ = self.collect()
        if err and sid in listed:
            # 지우지 못했다 — 복원 정보를 되돌리고 실패를 그 줄에 보인다
            with registry.editing(self.path) as data:
                data["deleted"] = [d for d in data["deleted"] if d["id"] != sid]
            self._fail(sid, f"{display_name(row)} 삭제 실패 — {err}")
            return
        if err:
            # 실패를 알렸는데 목록에서는 빠졌다 — 복원 정보는 남기고, 줄이 없으니 실패는
            # [삭제한 세션] 칸에 보인다 (줄에만 붙이면 아무 데도 안 보였다)
            self._failures[f"deleted:{sid}"] = (
                f"{display_name(row)} — 목록에서는 빠졌지만 claude rm 이 실패를 알렸습니다: {err}",
                time.time(),
            )
        with registry.editing(self.path) as data:
            registry.forget(data, sid)
        self._remove_older(row)
        self._ops.pop(sid, None)
        self._drop_cache()

    def _remove_older(self, row: dict) -> None:
        """최근 세대를 지운 뒤 숨겨 둔 옛 세대 줄도 Claude 목록에서 지운다. 거절된 것(다른 세션이
        작업용 복사본을 씀 등)은 이유를 [삭제한 세션] 칸에 보인다 — 최근 세대 줄은 이미 없다."""
        olds = [o for o in row.get("olderGenerations") or [] if o]
        if not olds:
            return
        listed, _ = self.collect()
        refused = []
        for old in olds:
            if pid := (listed.get(old) or {}).get("pid"):
                kill_tree(pid)
            try:
                err = agents.remove(old)
            except Exception as exc:
                err = str(exc) or type(exc).__name__
            if err:
                refused.append(f"{old} — {err}")
            else:
                with registry.editing(self.path) as data:
                    registry.forget(data, old)
        if refused:
            self._failures[f"deleted:{row['id']}"] = (
                f"{display_name(row)} 의 옛 세대 중 지우지 못한 것: " + "; ".join(refused),
                time.time(),
            )

    def restore(self, deleted_id: str) -> Result:
        """삭제한 세션을 마지막 대화로 다시 띄운다. 같은 목록 번호 · 이름으로 돌아오고(대화 번호에서
        목록 번호가 나온다) 그룹을 되돌린다."""
        key = f"deleted:{deleted_id}"
        record = next(
            (d for d in registry.load(self.path)["deleted"] if d["id"] == deleted_id), None
        )
        if record is None:
            return "삭제한 세션 목록에 없습니다"
        if self._in_progress(deleted_id):
            return "이미 처리 중입니다"
        if chat.transcript(record.get("sessionId") or "") is None:
            return "대화가 정리돼 복원할 수 없습니다"
        if not os.path.isdir(record.get("cwd") or ""):
            return f"폴더가 없습니다: {record.get('cwd')}"
        self._ops[deleted_id] = ("restore", time.time())
        self._failures.pop(key, None)
        self._background(self._restore, record)
        return None

    def _restore(self, record: dict) -> None:
        name = record.get("name") or record["id"][:8]
        before = {r.get("id") for r in agents.all_sessions()}
        err = agents.restore(
            record["cwd"],
            record["sessionId"],
            record.get("flags") or [],
            record.get("name") or None,
        )
        row = None
        if not err:
            row = agents.wait_for(
                lambda r: r.get("id") and r["id"] not in before and r.get("cwd") == record["cwd"]
            )
            if row is None:
                err = "다시 띄운 세션을 목록에서 찾지 못했습니다"
        if err:
            self._ops.pop(record["id"], None)
            self._failures[f"deleted:{record['id']}"] = (f"{name} 복원 실패 — {err}", time.time())
            return
        with registry.editing(self.path) as data:
            data["deleted"] = [d for d in data["deleted"] if d["id"] != record["id"]]
            group = record.get("group")
            if group in registry.group_names(data):
                registry.place(data, row["id"], group, self._ids_in(data, group) + [row["id"]])
        self._ops.pop(record["id"], None)
        self._drop_cache()

    # --- 새로 만들기 ---

    def create(self, cwd: str, name: str, group: str | None, force: bool = False) -> Result:
        """그 폴더에서 새 백그라운드 세션을 만들고 group 에 넣는다. 몇 초 걸린다."""
        name = (name or "").strip()
        if not name:
            return "이름을 적어 주세요"
        if not os.path.isdir(cwd or ""):
            return f"폴더가 없습니다: {cwd}"
        data = registry.load(self.path)
        if group is not None and group not in registry.group_names(data):
            return f"없는 그룹입니다: {group}"
        sessions, _ = self.collect()
        same = [r for r in sessions.values() if r.get("name") == name]
        if same and not force:
            return {
                "confirm": f"'{name}' 이름의 세션이 이미 {len(same)}개 있습니다. 그래도 만들까요?"
            }
        before = set(sessions)
        if err := agents.create(cwd, name):
            return err
        self._drop_cache()
        row = agents.wait_for(
            lambda r: r.get("id") and r["id"] not in before and r.get("name") == name, interval=1
        )
        if row is None:
            return (
                "세션을 만들었지만 목록에서 아직 찾지 못했습니다."
                " 잠시 뒤 '분류 안 됨'을 확인해 주세요"
            )
        if group is not None:
            with registry.editing(self.path) as data:
                registry.place(data, row["id"], group, self._ids_in(data, group) + [row["id"]])
        return None

    # --- 그룹 · 배치 ---

    def _ids_in(self, data: dict, group: str | None) -> list[str]:
        """그 그룹의 지금 세션 순서 (목록에 있는 것만)."""
        sessions, _ = self.collect()
        ids = [sid for sid in sessions if registry.group_of(data, sid) == group]
        return sorted(
            ids, key=lambda s: (registry.order_of(data, s) is None, registry.order_of(data, s) or 0)
        )

    def edit(self, change: Callable[[dict], object]) -> Result:
        """배치를 고친다 (그룹 만들기 · 이름 바꾸기 · 지우기 · 접기 · 옮기기)."""
        try:
            with registry.editing(self.path) as data:
                change(data)
        except (ValueError, TimeoutError) as exc:
            return str(exc)
        return None

    def place(self, session_id: str, group: str | None, ordered_ids: list[str]) -> Result:
        if session_id not in self.collect()[0]:
            return "목록에 없는 세션입니다"
        return self.edit(lambda data: registry.place(data, session_id, group, ordered_ids))
