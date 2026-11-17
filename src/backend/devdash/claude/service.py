"""화면의 세션 버튼 동작 — 연결 · 해제 · 모두 연결 · 포그라운드.

세션은 대기 중에도 하나에 ~250MB 를 먹고, 메모리가 모자라면 Claude 관리자가 쉬는 세션을
끈다. 그래서 쓸 것만 켜 두도록 하나씩 켜고 끄는 화면을 둔다.

버튼 동작의 응답은 셋 중 하나다.
  None              성공
  str               실패 이유 (화면 안내 상자에 띄운다)
  {"confirm": 말}   한 번 물어봐야 하는 경우 (확인하면 force 로 다시 부른다)
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from devdash.claude import agents, chat
from devdash.claude.registry import DEFAULT_SESSIONS_PATH, by_group, load
from devdash.local import kill_tree, open_terminal

OP_TIMEOUT = 40
"""초. 연결/해제가 이 안에 반영되지 않으면 '바뀌는 중' 표시를 푼다."""

FAIL_SHOW = 60
"""초. 연결 실패 이유를 화면 갱신에 실어 보내는 시간. 화면은 같은 실패를 한 번만 띄운다."""

LIVE_CACHE_SECONDS = 2
"""claude agents 는 1~2초 걸린다. 화면이 여러 번 물어도 이 안이면 한 번만 부른다."""

QUEUED = float("inf")
"""모두 연결에서 차례를 기다리는 세션의 표시 시각. 기다리는 동안 OP_TIMEOUT 에 걸리지 않고,
차례가 오면 실제 시각으로 바뀐다."""

Result = str | dict | None


class SessionService:
    def __init__(self, path: Path = DEFAULT_SESSIONS_PATH):
        self.path = path
        self._ops: dict[str, tuple[str, float]] = {}  # 번호 → ("start"|"stop", 시각)
        self._failures: dict[str, tuple[str, float]] = {}  # 번호 → (이유, 시각)
        self._live: tuple[float, list] = (0.0, [])
        self._lock = threading.Lock()

    def _background(self, fn, *args) -> None:
        """오래 걸리는 일(이어 띄우기·명단 대기)은 뒤에서 돌리고 응답은 바로 준다.
        시험은 이 메서드만 바꿔 끼워 실제로 띄우지 않는다."""
        threading.Thread(target=fn, args=args, daemon=True).start()

    # --- 조회 ---

    def _cached_live(self) -> list:
        with self._lock:
            if time.time() - self._live[0] > LIVE_CACHE_SECONDS:
                self._live = (time.time(), agents.live_sessions())
            return self._live[1]

    def _drop_cache(self) -> None:
        with self._lock:
            self._live = (0.0, [])

    def entry(self, session_id: str) -> dict | None:
        return next((e for e in load(self.path) if e["id"] == session_id), None)

    def rows(self) -> list[dict]:
        live = self._cached_live()
        rows = []
        for e in load(self.path):
            s = agents.find_live(e, live)
            if not s:
                state = "off"
            elif s.get("kind") == "interactive":
                state = "terminal"  # 터미널에서 연 세션은 명령으로 끌 수 없다
            else:
                state = "on"
            op = self._ops.get(e["id"])
            if op:
                # 해제는 '꺼짐'으로 보여도 끝난 게 아니다 — stop 이 관리자 명단에서 빠진 걸
                # 확인하고 직접 표시를 푼다. 그 전에 풀면 연결이 눌려 복사본이 생긴다.
                done = op[0] == "start" and state != "off"
                if done or time.time() - op[1] > OP_TIMEOUT:
                    self._ops.pop(e["id"], None)
                else:
                    state = "starting" if op[0] == "start" else "stopping"
            row = {
                "id": e["id"],
                "name": e["name"],
                "group": e.get("group", ""),
                "state": state,
                # 컨텍스트 토큰 수 · 압축 횟수 (T-21). 꺼진 세션도 마지막 상태를 보인다
                **chat.context(e["id"]),
            }
            if s:
                # 켜진 세션이 지금 무엇을 하나: working(답을 만드는 중) · blocked(사람의 확인이나
                # 답을 기다림) · done(쉬는 중). 프로젝트 탭 [세션] 목록이 보인다 (T-9)
                row["activity"] = s.get("state")
            fail = self._failures.get(e["id"])
            if fail and time.time() - fail[1] <= FAIL_SHOW:
                row["error"], row["errorAt"] = fail
            rows.append(row)
        return rows

    def _in_progress(self, entry: dict) -> bool:
        """띄운 세션이 목록에 나타나기까지 몇 초 걸린다. 그 사이 또 누르면 resume 이 두 번
        돌아 복사본이 생기므로, 진행 중이면 같은 세션의 요청을 받지 않는다."""
        op = self._ops.get(entry["id"])
        return bool(op) and time.time() - op[1] <= OP_TIMEOUT

    # --- 연결 ---

    def _resume_in_order(self, entries: list[dict]) -> None:
        for e in entries:
            self._ops[e["id"]] = ("start", time.time())
            self._failures.pop(e["id"], None)
            # 막 해제한 세션은 관리자 명단에서 빠질 때까지 기다린다. 끝내 안 빠지면 띄우지
            # 않는다 — 띄우면 복사본이 생긴다.
            if not agents.wait_roster_clear(e["id"]):
                err = "아직 정리 중이라 연결하지 못했습니다. 잠시 뒤 다시 눌러 주세요"
            else:
                try:
                    err = agents.resume(e) or agents.confirm_started(e)
                except Exception as ex:
                    err = f"연결 중 오류: {ex!r}"
            if err:
                # '연결 중'에 묶어 두지 않고 바로 다시 누를 수 있게 풀고, 이유를 화면에 넘긴다
                self._ops.pop(e["id"], None)
                self._failures[e["id"]] = (f"{e['name']} 연결 실패 — {err}", time.time())

    def start(self, entry: dict) -> Result:
        if self._in_progress(entry):
            return "이미 처리 중입니다"
        if agents.find_live(entry, agents.live_sessions()):
            return "이미 켜져 있습니다"
        if not os.path.isdir(entry["project"]):
            return f"폴더가 없습니다: {entry['project']}"
        self._ops[entry["id"]] = ("start", time.time())
        self._background(self._resume_in_order, [entry])
        self._drop_cache()
        return None

    def start_all(self) -> Result:
        """꺼진 세션을 전부, 화면과 같은 순서로 하나씩 차례로 띄운다. 재부팅 뒤에 쓴다."""
        live = agents.live_sessions()
        targets = [
            e
            for _, entries in by_group(load(self.path))
            for e in entries
            if not self._in_progress(e)
            and not agents.find_live(e, live)
            and os.path.isdir(e["project"])
        ]
        if not targets:
            return "연결할 꺼진 세션이 없습니다"
        # 먼저 전부 '연결 중'으로 표시해 둔다 — 차례를 기다리는 세션을 또 눌러도 거부되게
        for e in targets:
            self._ops[e["id"]] = ("start", QUEUED)
        self._background(self._resume_in_order, targets)
        self._drop_cache()
        return None

    # --- 해제 ---

    def stop(self, entry: dict, force: bool = False) -> Result:
        if self._in_progress(entry):
            return "이미 처리 중입니다"
        s = agents.find_live(entry, agents.live_sessions())
        if not s:
            return "이미 꺼져 있습니다"
        if s.get("kind") == "interactive":
            return "터미널에서 연 세션은 그 터미널을 닫아야 꺼집니다"
        # 프로세스를 끊는 방식이라 하던 일이 중간에 멈춘다. 답을 만드는 중이면 끄지 않는다
        if s.get("state") == "working":
            return "작업 중입니다. 끝난 뒤 해제해 주세요"
        # 대화는 끝났는데 status 가 busy 면 세션이 띄운 백그라운드 작업(서버 등)이 도는 것이다.
        # 해제하면 함께 꺼지므로 한 번 묻는다
        if s.get("status") == "busy" and not force:
            n = agents.bg_task_count(s)
            count = f" {n}개" if n else ""
            return {
                "confirm": f"{entry['name']} 세션에서 서버 같은 백그라운드 작업이{count} "
                "실행 중입니다.\n해제하면 이 작업도 함께 꺼집니다. 해제할까요?"
            }
        self._ops[entry["id"]] = ("stop", time.time())
        # claude stop 대신 프로세스를 끊는다. stop 은 세션을 '사용자가 끔'으로 기록해 Claude 앱
        # 목록에서 사라지게 하고, 끊으면 재부팅 때처럼 '연결 끊김'으로 남는다.
        kill_tree(s["pid"])

        # 관리자 명단에서 빠질 때(약 5초)까지 '해제 중'을 유지한 뒤 푼다
        def finish() -> None:
            agents.wait_roster_clear(s.get("sessionId", ""), limit=OP_TIMEOUT)
            self._ops.pop(entry["id"], None)

        self._background(finish)
        self._drop_cache()
        return None

    # --- 포그라운드 ---

    def foreground(self, entry: dict) -> Result:
        """터미널 창에서 연다. 꺼진 세션은 이어 열고(창을 닫으면 꺼짐), 켜진 백그라운드
        세션은 붙기만 한다(창을 닫아도 계속 돎). 같은 대화를 포그라운드로 또 열면
        복사본이 생기기 때문이다."""
        if self._in_progress(entry):
            return "이미 처리 중입니다"
        s = agents.find_live(entry, agents.live_sessions())
        if s and s.get("kind") == "interactive":
            return "이미 터미널에서 열려 있습니다"
        if s:
            args = ["claude", "attach", s["id"]]
        else:
            if not os.path.isdir(entry["project"]):
                return f"폴더가 없습니다: {entry['project']}"
            if agents.in_roster(entry["id"]):
                return "아직 정리 중입니다. 잠시 뒤 다시 눌러 주세요"
            args = ["claude", "--resume", entry["id"]]
            # [터미널]로 바뀔 때까지 '연결 중'으로 둔다 — 그 사이 또 누르면 두 번 열린다
            self._ops[entry["id"]] = ("start", time.time())
        open_terminal(entry["project"], entry["name"], args)
        self._drop_cache()
        return None
