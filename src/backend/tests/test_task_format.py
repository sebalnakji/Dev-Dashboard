"""할 일 파일 형식 검사기(plugins/devdash/scripts/devdash_tasks.py) 테스트.

검사기는 플러그인 안에 있고 표준 라이브러리만 쓴다(훅에서 불린다). 대시보드도 같은 정의를
쓰므로, 여기서 형식이 조용히 바뀌지 않게 지킨다. 템플릿이 형식에 맞는지도 함께 본다 —
템플릿과 검사기가 어긋나면 새로 만든 할 일이 모두 '형식 오류'가 된다.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[3] / "plugins" / "devdash"
_spec = importlib.util.spec_from_file_location(
    "devdash_tasks", PLUGIN / "scripts" / "devdash_tasks.py"
)
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

TEMPLATE = (PLUGIN / "skills" / "records" / "task-template.md").read_text(encoding="utf-8")


def task(
    status="doing", goal="세션 칸에 확인 창을 단다", done_items="", num=3, title="확인 창", **meta
):
    head = {"status": status, "session": "develope", "created": "2026-11-12"}
    head.update(meta)
    lines = ["---", *[f"{k}: {v}" for k, v in head.items() if v is not None], "---", ""]
    lines += [f"# T-{num} {title}", "", "## 목표", "", goal, "", "## 한 일", "", done_items, ""]
    lines += ["## 남은 일", ""]
    return "\n".join(lines)


def write(folder: Path, name: str, text: str) -> Path:
    p = folder / name
    p.write_text(text, encoding="utf-8", newline="")
    return p


# --- 템플릿 ---


def fill_template(goal: str = "확인 창을 단다") -> str:
    """템플릿을 Claude 가 하듯 채운다. 주석 문구가 바뀌어도 깨지지 않게 목표 항목의 주석만 갈아 끼운다."""
    text = TEMPLATE.replace("# T-0 ", "# T-1 ").replace("created:", "created: 2026-11-12", 1)
    return re.sub(r"(## 목표\n\n)<!--.*?-->", rf"\g<1>{goal}", text, flags=re.S)


def test_템플릿은_항목을_모두_갖추고_채우면_형식에_맞는다(tmp_path):
    data = dt.parse(TEMPLATE)
    assert set(data["meta"]) == set(dt.META_KEYS)
    assert list(data["sections"]) == list(dt.SECTIONS)
    assert dt.problems(write(tmp_path, "T-1-확인-창.md", fill_template())) == []


def test_템플릿_그대로면_빈_값과_빈_목표를_알린다(tmp_path):
    reasons = dt.problems(write(tmp_path, "T-1-x.md", TEMPLATE))
    assert "머리말 'created' 가 비어 있습니다" in reasons
    assert "'목표' 가 비어 있습니다" in reasons
    assert any("첫 제목은 '# T-1 제목'" in r for r in reasons)  # 템플릿은 T-0


# --- 검사: 통과 ---


def test_진행_중이면_한_일이_비어도_된다(tmp_path):
    assert dt.problems(write(tmp_path, "T-3-확인-창.md", task())) == []


def test_완료이고_한_일이_있으면_통과(tmp_path):
    text = task(
        status="done", done_items="- 2026-11-12 해제 버튼에 확인 창 추가 — 서버가 함께 꺼지므로"
    )
    assert dt.problems(write(tmp_path, "T-3-x.md", text)) == []


def test_BOM_과_CRLF_가_있어도_읽는다(tmp_path):
    """윈도우 편집기는 BOM 과 CRLF 줄바꿈을 붙이곤 한다."""
    text = "﻿" + task().replace("\n", "\r\n")
    assert dt.parse(text)["meta"]["status"] == "doing"
    assert dt.problems(write(tmp_path, "T-3-x.md", text)) == []


def test_제목에_콜론이_있어도_읽는다():
    data = dt.parse(task(title="API: 세션 목록"))
    assert dt.title_of(data, "3") == "API: 세션 목록"


# --- 검사: 막기 ---


def test_완료인데_한_일이_비면_막는다(tmp_path):
    assert "완료(done)인데 비어 있는 항목: 한 일" in dt.problems(
        write(tmp_path, "T-3-x.md", task(status="done"))
    )


def test_안내_주석만_있으면_빈_것으로_보고_닫히지_않은_주석도_그렇다(tmp_path):
    for items in ("<!-- 여기에 -->", "<!-- 닫히지 않은 주석\n- 2026-11-12 무엇"):
        reasons = dt.problems(write(tmp_path, "T-3-x.md", task(status="done", done_items=items)))
        assert "완료(done)인데 비어 있는 항목: 한 일" in reasons


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("task-3.md", task(), "파일 이름"),
        ("T-03-x.md", task(), "파일 이름"),  # 번호 앞의 0
        ("T-4-x.md", task(), "첫 제목은 '# T-4 제목'"),  # 파일 번호와 제목 줄 번호가 다름
        ("T-3-x.md", task(title=""), "첫 제목은"),
        ("T-3-x.md", task(status="wip"), "status 는"),
        ("T-3-x.md", task(session=None), "머리말에 'session' 가 없습니다"),
        ("T-3-x.md", task(created=""), "머리말 'created' 가 비어 있습니다"),
        ("T-3-x.md", task(created="어제"), "created 는 실제 날짜(YYYY-MM-DD)"),
        ("T-3-x.md", task().replace("## 남은 일", "## 다음"), "없는 항목(## 제목): 남은 일"),
        ("T-3-x.md", task() + "\n## 한 일\n\n- 또\n", "'## 한 일' 항목이 두 번"),
        (
            "T-3-x.md",
            task(status="todo").replace("created:", "status: done\ncreated:"),
            "'status' 가 두 번",
        ),
        ("T-3-x.md", task().replace("---\n\n# T-3", "\n# T-3"), "머리말이 닫히지 않았습니다"),
    ],
)
def test_형식이_어긋나면_이유를_알린다(tmp_path, name, text, expected):
    assert any(expected in r for r in dt.problems(write(tmp_path, name, text)))


def test_session_은_todo_면_비어도_되고_doing_이면_비면_안_된다(tmp_path):
    """todo 는 아직 아무도 안 잡은 것. doing 은 누가 잡았는지 알아야 고친 파일을 붙인다."""
    assert dt.problems(write(tmp_path, "T-3-x.md", task(status="todo", session=""))) == []
    reasons = dt.problems(write(tmp_path, "T-3-x.md", task(session="")))
    assert "진행 중(doing)인데 session 이 비어 있습니다" in reasons


def test_달력에_없는_날짜는_막는다(tmp_path):
    reasons = dt.problems(write(tmp_path, "T-3-x.md", task(created="2026-13-45")))
    assert any("실제 날짜" in r for r in reasons)


def test_완료인데_한_일이_목록_기호뿐이면_빈_것으로_본다(tmp_path):
    reasons = dt.problems(write(tmp_path, "T-3-x.md", task(status="done", done_items="-\n- ")))
    assert "완료(done)인데 비어 있는 항목: 한 일" in reasons


def test_머리말_앞에_빈_줄이_있으면_원인을_알려_준다(tmp_path):
    reasons = dt.problems(write(tmp_path, "T-3-x.md", "\n" + task()))
    assert any("첫 줄의 '---'" in r for r in reasons)


# --- 검사: 읽기 견고함 ---


def test_코드_블록_안의_제목은_항목으로_읽지_않는다():
    """기록 형식 자체를 다루는 작업에서 실제로 나온다."""
    body = "- 2026-11-12 예시를 넣음\n\n```md\n## 남은 일\n## 설치\n```\n\n~~~\n## 목표\n~~~"
    data = dt.parse(task(status="done", done_items=body))
    assert list(data["sections"]) == ["목표", "한 일", "남은 일"]
    assert "## 설치" in data["sections"]["한 일"]
    assert data["sections"]["목표"] == "세션 칸에 확인 창을 단다"


@pytest.mark.parametrize(
    "block",
    [
        "~~~\n```md\n## 남은 일\n```\n~~~",  # 섞인 표식: ~~~ 안의 ``` 는 블록을 닫지 않는다
        "````\n```\n## 설치\n```\n````",  # 긴 표식 안의 짧은 표식
        "<!-- 예시\n## 한 일\n-->",  # 여러 줄 주석 안의 제목
    ],
)
def test_섞인_코드_블록과_주석_안의_제목도_항목이_아니다(block):
    data = dt.parse(task(status="done", done_items="- 2026-11-12 예시\n\n" + block))
    assert list(data["sections"]) == ["목표", "한 일", "남은 일"]
    assert data["issues"] == []


def test_한_줄_안에서_닫히는_코드는_블록을_열지_않는다(tmp_path):
    """'```foo``` 를 고침' 이 블록을 열면 뒤 항목을 다 삼켜 '없는 항목' 오탐이 난다."""
    text = task(status="done", done_items="```foo``` 를 고침")
    assert dt.problems(write(tmp_path, "T-3-x.md", text)) == []


def test_머리말의_들여쓴_줄과_목록은_건너뛰고_다른_키는_무시한다():
    """머리말은 세 키만 읽는다. 다른 키(tags 등)는 있어도 막지 않는다."""
    data = dt.parse(task().replace("created:", "tags:\n  - a\n- b: c\ncreated:"))
    assert set(data["meta"]) == {"status", "session", "tags", "created"}


def test_규칙_문서와_검사기가_같은_항목과_키를_말한다():
    """SKILL.md 가 다른 제목이나 키를 가르치면 Claude 가 쓴 파일이 모두 형식 오류가 된다."""
    skill = (PLUGIN / "skills" / "records" / "SKILL.md").read_text(encoding="utf-8")
    for section in dt.SECTIONS:
        assert f"## {section}" in skill
    for key in dt.META_KEYS:
        assert f"{key}:" in skill


def test_UTF8_이_아닌_파일은_예외_없이_이유로_알린다(tmp_path):
    p = tmp_path / "T-3-x.md"
    p.write_bytes(task().encode("utf-16"))
    assert any("읽을 수 없습니다" in r for r in dt.problems(p))


# --- 폴더 검사 · 커밋 연결 ---


def test_같은_번호의_파일이_둘이면_둘_다_알린다(tmp_path):
    write(tmp_path, "T-10-a.md", task(num=10))
    write(tmp_path, "T-10-b.md", task(num=10))
    report = dt.check_dir(tmp_path)
    assert set(report) == {"T-10-a.md", "T-10-b.md"}
    assert all("여럿입니다" in r[0] for r in report.values())


def test_폴더_이름이_할_일_이름과_같아도_건너뛴다(tmp_path):
    (tmp_path / "T-80-dir.md").mkdir()
    write(tmp_path, "T-3-ok.md", task())
    assert dt.check_dir(tmp_path) == {}


def test_없는_폴더는_문제_없음이_아니라_오류다(tmp_path):
    with pytest.raises(FileNotFoundError):
        dt.check_dir(tmp_path / "없음")
    assert dt.main(["x", "check", str(tmp_path / "없음")]) == 2


def test_check_명령은_이름이_틀린_파일도_알리고_오류가_있으면_1(tmp_path, capsys):
    write(tmp_path, "T-3-ok.md", task())
    assert dt.main(["x", "check", str(tmp_path)]) == 0
    write(tmp_path, "todo.md", "x")
    assert dt.main(["x", "check", str(tmp_path)]) == 1
    assert "todo.md" in capsys.readouterr().out
    assert dt.main(["x"]) == 2


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("탭 분리 [T-12] [T-13]\n\n본문 [T-14]", ["12", "13", "14"]),
        ("소문자 [t-12], 쉼표 [T-12, T-13], 공백 [ T-12 ]", []),
        ("앞의 0 [T-012]", []),
    ],
)
def test_커밋_메시지의_할_일_번호(message, expected):
    assert dt.TASK_REF.findall(message) == expected


# --- 머리말 값 바꾸기 (세션 이름이 바뀔 때 훅이 쓴다) ---

HEAD = "---\nstatus: doing\nsession: old\ncreated: 2026-11-12\n---\n\n# T-1 x\n"


def test_머리말_값을_바꾸고_나머지는_그대로_둔다():
    assert dt.set_meta(HEAD, "session", "new") == HEAD.replace("session: old", "session: new")


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_줄바꿈과_BOM_을_지킨다(ending):
    text = "\ufeff" + HEAD.replace("\n", ending)
    assert dt.set_meta(text, "session", "new") == text.replace("session: old", "session: new")


def test_본문의_같은_모양_줄은_건드리지_않는다():
    """정규식으로 바꾸던 때는 머리말을 못 맞추면 본문 줄을 바꿨다."""
    text = HEAD.replace("session: old", "session : old") + "\n```\nsession: old\n```\n"
    out = dt.set_meta(text, "session", "new")
    assert dt.parse(out)["meta"]["session"] == "new"
    assert out.endswith("```\nsession: old\n```\n")


@pytest.mark.parametrize("name", [r"fix C:\work", r"a\1b", r"x\g<0>y", "tab\there"])
def test_역슬래시가_든_이름도_글자_그대로_넣는다(name):
    """바꿀 값을 정규식 치환 문법으로 읽어 예외가 나거나 글이 깨졌다."""
    assert dt.parse(dt.set_meta(HEAD, "session", name))["meta"]["session"] == name


def test_머리말이_없거나_키가_없으면_그대로다():
    assert dt.set_meta("# T-1 x\nsession: old\n", "session", "new") == "# T-1 x\nsession: old\n"
    assert dt.set_meta(HEAD, "owner", "new") == HEAD


@pytest.mark.parametrize("raw", ['"develope"', "'develope'", '" develope "'])
def test_머리말_값의_따옴표는_뗀다(raw):
    """YAML 습관으로 감싼 따옴표. 떼지 않으면 세션 이름이 달라 보였다."""
    assert (
        dt.parse(HEAD.replace("session: old", f"session: {raw}"))["meta"]["session"] == "develope"
    )
