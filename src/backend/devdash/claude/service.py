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


def display_name(row: dict) -> str:
    """Claude 쪽 이름. 비었으면 '폴더 이름 · 번호 앞 8자리' — 이름 없는 세션도 가려 볼 수 있게."""
    return row.get("name") or f"{agents.folder_label(row.get('cwd') or '')} · {row['id'][:8]}"


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
        threading.Thread(target=fn, args=args, daemon=True).start()

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
        out = {
            "id": sid,
            "name": display_name(row),
            "named": bool(row.get("name")),
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
        if row.get("pid"):
            # 켜진 세션이 지금 무엇을 하나: working(답을 만드는 중) · blocked(사람의 확인이나
            # 답을 기다림) · done(쉬는 중). 프로젝트 탭 [세션] 목록도 보인다
            out["activity"] = row.get("state")
        fail = self._failures.get(sid)
        if fail and time.time() - fail[1] <= FAIL_SHOW:
            out["error"], out["errorAt"] = fail
        return out

    def rows(self) -> list[dict]:
        """백그라운드 세션 줄 — 그룹 순서, 그룹 안 순서, 이름 순."""
        data = registry.load(self.path)
        sessions, _ = self.collect()
        rows = [self._row(r, data) for r in sessions.values()]
        rank = {g: i for i, g in enumerate(registry.group_names(data))}

        def key(r: dict):
            group = rank.get(r["group"], len(rank)) if r["group"] else len(rank)
            order = r["order"] if r["order"] is not None else 1 << 30
            return (group, order, r["name"].casefold())

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

    def start(self, row: dict) -> Result:
        if self._in_progress(row["id"]):
            return "이미 처리 중입니다"
        if row.get("pid"):
            return "이미 켜져 있습니다"
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
        self._background(self._delete, row)
        self._drop_cache()
        return None

    def _delete(self, row: dict) -> None:
        sid = row["id"]
        # 지우면 관리자 기록(jobs/<번호>)도 사라지므로 마지막 대화 번호를 먼저 읽어 둔다
        conversation = self.conversation(row)
        if row.get("pid"):
            kill_tree(row["pid"])
            agents.wait_roster_clear(row.get("sessionId") or "", limit=OP_TIMEOUT)
        if err := agents.remove(sid):
            self._fail(sid, f"{display_name(row)} 삭제 실패 — {err}")
            return
        with registry.editing(self.path) as data:
            data["deleted"].append(
                {
                    "id": sid,
                    "name": row.get("name") or "",
                    "group": registry.group_of(data, sid),
                    "cwd": row.get("cwd") or "",
                    "sessionId": conversation or "",
                    "deletedAt": time.time(),
                }
            )
            registry.forget(data, sid)
        self._ops.pop(sid, None)
        self._drop_cache()

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
        err = agents.restore(record["cwd"], record["sessionId"])
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
