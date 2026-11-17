"""`python -m devdash claude ...` 명령.

list / add / remove 는 세션 목록을 터미널에서 다룰 때, hook-end / hook-start 는
Claude Code 의 /clear 훅이 부를 때 쓴다.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

from devdash.claude import agents, hooks
from devdash.claude.registry import by_group, editing, find, load, norm
from devdash.cli import pad


def drift_note(entry: dict, live: dict) -> str | None:
    """살아있는 세션의 대화 번호가 목록 번호와 다르면 갱신 안내를 돌려준다.

    /clear 훅이 자동으로 고치지만, 훅이 안 걸려 있던 때의 어긋남은 이걸로 드러난다.
    """
    sid = live.get("sessionId", "")
    if not sid or sid.startswith(entry["id"]):
        return None
    return (
        f"⚠ 대화 번호가 바뀜 (/clear?)  목록 {entry['id'][:8]} → 실제 {sid[:8]}\n"
        f"      갱신: claude add {sid[:8]} --name {entry['name']} --group {entry.get('group')}"
        f"  →  claude remove {entry['id'][:8]}"
    )


def cmd_list(path: Path) -> int:
    entries = load(path)
    if not entries:
        print("등록된 세션이 없습니다.  claude add <번호 앞자리> 로 추가합니다.")
        return 0
    live = agents.live_sessions()
    alive = sum(1 for e in entries if agents.find_live(e, live))
    print(f"등록 {len(entries)}개  ·  켜짐 {alive}  ·  꺼짐 {len(entries) - alive}\n")
    for group, members in by_group(entries):
        print(f"[{group}]")
        for e in members:
            s = agents.find_live(e, live)
            proj = os.path.basename(norm(e["project"]))
            print(f"  {'●' if s else '○'} {pad(e['name'], 16)} {pad(proj, 24)} {e['id'][:8]}")
            note = s and drift_note(e, s)
            if note:
                print(f"    {note}")
        print()

    # 목록에 없는 실행 중 세션을 추가 후보로 보여준다
    ids = {e["id"][:8] for e in entries}
    names = {e.get("name") for e in entries}
    cand = [
        s
        for s in live
        if s.get("sessionId", "")[:8] not in ids and (s.get("name") or "") not in names
    ]
    if cand:
        print("등록 안 된 실행 중 세션 (추가 후보):")
        for s in cand:
            cwd = os.path.basename(norm(s.get("cwd", "")))
            print(f"  {s['sessionId'][:8]}  {pad(s.get('name') or '-', 16)} {cwd}")
    return 0


def cmd_add(path: Path, key: str, name: str | None, group: str | None, project: str | None) -> int:
    matches = {
        s["sessionId"]: s for s in agents.all_sessions() if s.get("sessionId", "").startswith(key)
    }
    if not matches:
        print(f"'{key}' 로 시작하는 세션을 찾지 못했습니다. claude list 로 확인합니다.")
        return 1
    if len(matches) > 1:
        print("여러 개가 일치합니다. 더 길게 지정합니다:")
        for s in matches.values():
            print(f"  {s['sessionId']}  {s.get('name')}  {s.get('cwd')}")
        return 1
    s = next(iter(matches.values()))
    entry = {
        "id": s["sessionId"],
        "name": name or s.get("name") or s["sessionId"][:8],
        "group": group or "default",
        # worktree 에서 일하던 세션은 cwd 가 작업용 복사본이라, 원래 프로젝트를 따로 줄 수 있다
        "project": norm(project or s.get("cwd", "")),
    }
    with editing(path) as entries:
        same = next((e for e in entries if e["id"] == entry["id"]), None)
        if same:
            same.update(entry)  # 다시 넣어도 프로젝트 소속(project_key) 같은 다른 필드는 지킨다
        else:
            entries.append(entry)
    print(f"추가됨: [{entry['group']}] {entry['name']}  {entry['project']}  ({entry['id'][:8]})")
    return 0


def cmd_remove(path: Path, key: str) -> int:
    with editing(path) as entries:
        hit = find(entries, key)
        entries[:] = [e for e in entries if e not in hit]
    if not hit:
        print(f"'{key}' 와 일치하는 항목이 없습니다.")
        return 1
    print(f"{len(hit)}개 제거됨: {', '.join(e['name'] for e in hit)}")
    return 0


def cmd_hook(which: str) -> int:
    """/clear 훅. 모든 세션의 /clear 에 끼어들므로 무슨 일이 있어도 세션을 방해하지 않는다."""
    try:
        # utf-8-sig: 앞에 BOM 이 붙어 들어와도 읽는다 (PowerShell 파이프로 시험할 때 붙었다)
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
        (hooks.on_end if which == "hook-end" else hooks.on_start)(data)
    except Exception as ex:
        with contextlib.suppress(Exception):
            hooks.log(f"오류 {which}: {ex!r}")
    return 0
