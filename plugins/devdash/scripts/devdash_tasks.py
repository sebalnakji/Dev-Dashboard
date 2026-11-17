"""할 일 파일(T-12-제목.md)을 읽고 형식을 검사한다.

형식의 정의는 이 파일 한 곳에만 둔다. 강제 훅(작업이 끝날 때 검사)과 대시보드(프로젝트 탭)가
같은 정의를 쓰게 하려는 것이다 (ADR 0006·0007). 훅에서 불리므로 표준 라이브러리만 쓴다.

할 일 파일은 저장소 밖 ~/.devdash/projects/<프로젝트>/tasks/ 에 있다. 형식:

    ---
    status: doing          todo · doing · done
    session: develope      맡은 세션 이름 (비워도 됨)
    created: 2026-11-12
    ---

    # T-12 제목           번호는 파일 이름에서, 제목은 이 줄에서만 읽는다

    ## 목표
    ## 한 일              완료(done)면 비어 있으면 안 된다
    ## 남은 일

    python devdash_tasks.py check <tasks 폴더>   형식이 어긋난 파일과 이유 (없으면 종료 코드 0)
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

STATUSES = ("todo", "doing", "done")
"""할 일 · 진행 중 · 한 일."""

META_KEYS = ("status", "session", "created")
MAY_BE_EMPTY = ("session",)

SECTIONS = ("목표", "한 일", "남은 일")
"""본문 항목. 제목을 바꾸면 대시보드가 못 읽으므로 고정한다."""

REQUIRED_WHEN_DONE = ("한 일",)
"""완료로 바꿀 때 비어 있으면 안 되는 항목. 완료한 작업에 정리가 없는 것만은 막는다.
바뀐 파일은 훅의 자동 변경 기록이 알므로 Claude 에게 쓰게 하지 않는다."""

FILE_NAME = re.compile(r"^T-([1-9]\d*)-.+\.md$")
"""T-12-제목.md. 번호 앞의 0(T-03)은 받지 않는다 — 같은 번호가 두 이름으로 생긴다."""

TASK_REF = re.compile(r"\[T-([1-9]\d*)\]")
"""커밋 메시지 첫 줄 끝의 할 일 번호. 예: '프로젝트 탭 파일 구조 [T-12] [T-13]'."""

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_COMMENT = re.compile(r"<!--.*?(-->|\Z)", re.S)
_BARE_BULLET = re.compile(r"^\s*[-*]\s*$", re.M)


def _opens_fence(line: str) -> str | None:
    """코드 블록을 여는 줄이면 그 표식. '```foo``` 를 고침' 처럼 한 줄 안에서 닫히는
    인라인 코드는 블록이 아니다 (백틱 표식 뒤에 백틱이 또 오면 블록을 열지 않는다)."""
    m = _FENCE.match(line)
    if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):
        return None
    return m.group(1)


def _closes_fence(line: str, opened: str) -> bool:
    """같은 종류이고 같거나 긴 표식만 블록을 닫는다 (```` 안의 ``` 는 닫지 않는다)."""
    m = _FENCE.match(line)
    return (
        bool(m)
        and not m.group(2).strip()
        and m.group(1)[0] == opened[0]
        and len(m.group(1)) >= len(opened)
    )


def parse(text: str) -> dict:
    """{'meta', 'title_line', 'sections', 'issues'} 으로 나눈다.

    issues 는 읽다가 발견한 구조 문제(닫히지 않은 머리말, 중복 키·항목)다.
    머리말은 'key: value' 한 줄씩만 받는다 (중첩 YAML 을 쓰지 않아 표준 라이브러리로 읽힌다).
    """
    text = text.removeprefix("﻿")  # 윈도우 편집기가 붙이는 BOM
    lines = text.splitlines()
    meta: dict[str, str] = {}
    issues: list[str] = []
    start = 0

    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            # 닫히지 않으면 본문의 '키: 값' 줄까지 머리말로 읽혀 상태를 덮어쓸 수 있다
            issues.append("머리말이 닫히지 않았습니다 (끝에 '---' 가 없음)")
            start = len(lines)
        else:
            for line in lines[1:end]:
                if not line.strip() or line[0].isspace() or line.lstrip().startswith("-"):
                    continue
                key, sep, value = line.partition(":")
                key = key.strip()
                if not sep:
                    continue
                if key in meta:
                    issues.append(f"머리말에 '{key}' 가 두 번 있습니다")
                meta[key] = _unquote(value.strip())
            start = end + 1

    title_line = None
    sections: dict[str, str] = {}
    current, buf = None, []
    fence = None  # 열린 코드 블록의 표식(예: ``` · ````). 그 안의 '## ' 는 제목이 아니다
    in_comment = False  # 여러 줄 <!-- --> 안의 '## ' 도 제목이 아니다
    for line in lines[start:]:
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
        elif in_comment:
            in_comment = "-->" not in line
        elif (opened := _opens_fence(line)) is not None:
            fence = opened
        elif "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            in_comment = True
        elif title_line is None and current is None and line.startswith("# "):
            title_line = line[2:].strip()
            continue
        elif m := re.match(r"^##\s+(.+?)\s*$", line):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current, buf = m.group(1), []
            if current in sections:
                issues.append(f"'## {current}' 항목이 두 번 있습니다")
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return {"meta": meta, "title_line": title_line, "sections": sections, "issues": issues}


def _unquote(value: str) -> str:
    """'session: "develope"' 처럼 YAML 습관으로 감싼 따옴표를 뗀다. 떼지 않으면 세션 이름이
    달라 보여 진행 중 할 일을 못 찾고 강제도 빠졌다 (4단계 Fable 재현)."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def is_filled(section_text: str | None) -> bool:
    """안내 주석(<!-- -->)과 내용 없는 목록 기호('-')를 빼고도 내용이 있나.
    닫히지 않은 주석은 끝까지 주석으로 본다."""
    if not section_text:
        return False
    return bool(_BARE_BULLET.sub("", _COMMENT.sub("", section_text)).strip())


def _is_date(value: str) -> bool:
    """모양(YYYY-MM-DD)만이 아니라 달력에 있는 날짜인가 (2026-13-45 는 아니다)."""
    try:
        return len(value) == 10 and date.fromisoformat(value) is not None
    except ValueError:
        return False


def title_of(data: dict, number: str) -> str | None:
    """'# T-12 제목' 에서 제목만. 번호가 파일 이름과 다르면 None."""
    line = data["title_line"] or ""
    prefix = f"T-{number} "
    return line[len(prefix) :].strip() or None if line.startswith(prefix) else None


def problems(path: Path) -> list[str]:
    """형식이 어긋난 이유들. 비어 있으면 맞는 것이다. 읽을 수 없어도 예외 없이 이유로 돌려준다."""
    m = FILE_NAME.match(path.name)
    if not m:
        return [f"파일 이름이 'T-번호-제목.md' 형식이 아닙니다: {path.name}"]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"읽을 수 없습니다 (UTF-8 이 아니거나 열 수 없음): {exc.__class__.__name__}"]

    data = parse(text)
    meta, sections = data["meta"], data["sections"]
    out = list(data["issues"])

    if not meta and "---" in text and not data["issues"]:
        out.append("머리말은 파일 첫 줄의 '---' 로 시작해야 합니다 (앞에 빈 줄이나 다른 글이 있음)")
    for key in META_KEYS:
        if key not in meta:
            out.append(f"머리말에 '{key}' 가 없습니다")
        elif not meta[key] and key not in MAY_BE_EMPTY:
            out.append(f"머리말 '{key}' 가 비어 있습니다")
    status = meta.get("status")
    if status and status not in STATUSES:
        out.append(f"status 는 {'/'.join(STATUSES)} 중 하나여야 합니다: {status!r}")
    if status == "doing" and not meta.get("session"):
        # 이 세션이 고친 파일을 이 할 일에 붙이려면 누가 잡고 있는지 알아야 한다
        out.append("진행 중(doing)인데 session 이 비어 있습니다")
    if meta.get("created") and not _is_date(meta["created"]):
        out.append(f"created 는 실제 날짜(YYYY-MM-DD)여야 합니다: {meta['created']!r}")

    if title_of(data, m.group(1)) is None:
        out.append(f"첫 제목은 '# T-{m.group(1)} 제목' 이어야 합니다: {data['title_line']!r}")

    absent = [s for s in SECTIONS if s not in sections]
    if absent:
        out.append(f"없는 항목(## 제목): {', '.join(absent)}")
    if "목표" in sections and not is_filled(sections["목표"]):
        out.append("'목표' 가 비어 있습니다")
    if status == "done":
        empty = [s for s in REQUIRED_WHEN_DONE if s in sections and not is_filled(sections[s])]
        if empty:
            out.append(f"완료(done)인데 비어 있는 항목: {', '.join(empty)}")
    return out


def read_meta(path: Path) -> dict[str, str]:
    """머리말만. 읽을 수 없으면 빈 것."""
    try:
        return parse(path.read_text(encoding="utf-8"))["meta"]
    except (OSError, UnicodeDecodeError):
        return {}


def set_meta(text: str, key: str, value: str) -> str:
    """머리말의 key 값을 바꾼 글. 머리말에 그 키가 없으면 그대로 돌려준다.

    parse 와 같은 규칙으로 줄을 읽어(키 앞뒤 공백 무시, 들여쓴 줄 건너뜀) 머리말 안에서만 바꾼다 —
    본문의 같은 모양 줄은 건드리지 않는다. 줄바꿈(LF·CRLF)과 BOM 도 그대로 둔다 (3단계 Fable 검토).
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].removeprefix("﻿").strip() != "---":
        return text
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            break
        name, sep, _ = line.partition(":")
        if sep and not line[0].isspace() and name.strip() == key:
            ending = line[len(line.rstrip("\r\n")) :]
            lines[i] = f"{key}: {value}{ending}"
            return "".join(lines)
    return text


def check_dir(tasks_dir: Path) -> dict[str, list[str]]:
    """폴더 안 할 일 파일마다 형식 문제. 파일 하나로는 알 수 없는 것도 알린다.

    - 같은 번호가 둘 이상: 여러 세션이 동시에 만들면 생기고, [T-번호] 커밋 연결이 모호해진다
    - 한 세션이 진행 중(doing) 둘 이상: 그 세션이 고친 파일을 어느 할 일에 붙일지 정할 수 없다
    """
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"할 일 폴더가 없습니다: {tasks_dir}")
    report: dict[str, list[str]] = {}
    by_number: dict[str, list[str]] = {}
    doing_by_session: dict[str, list[str]] = {}
    for p in sorted(x for x in tasks_dir.glob("*.md") if x.is_file()):
        reasons = problems(p)
        if reasons:
            report[p.name] = reasons
        m = FILE_NAME.match(p.name)
        if not m:
            continue
        by_number.setdefault(m.group(1), []).append(p.name)
        meta = read_meta(p)
        if meta.get("status") == "doing" and meta.get("session"):
            doing_by_session.setdefault(meta["session"], []).append(p.name)

    for number, names in by_number.items():
        if len(names) > 1:
            for name in names:
                report.setdefault(name, []).append(
                    f"T-{number} 번호를 쓰는 파일이 여럿입니다: {', '.join(names)}"
                )
    for session, names in doing_by_session.items():
        if len(names) > 1:
            for name in names:
                report.setdefault(name, []).append(
                    f"세션 '{session}' 의 진행 중 할 일이 여럿입니다: {', '.join(names)}"
                )
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "check":
        print(__doc__)
        return 2
    try:
        report = check_dir(Path(argv[2]))
    except FileNotFoundError as exc:
        print(exc)
        return 2
    for name, reasons in report.items():
        for reason in reasons:
            print(f"{name}: {reason}")
    return 1 if report else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv))
