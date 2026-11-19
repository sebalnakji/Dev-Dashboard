"""devdash 플러그인 권한 규칙 (사용자 설정에 두 줄 넣기) 테스트. 실제 ~/.claude 는 건드리지 않는다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devdash import plugin_perms


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / ".devdash"
    monkeypatch.setenv("DEVDASH_HOME", str(h))
    return h


def test_절대_경로는_권한_규칙에서_슬래시_둘로_시작한다(home):
    """'/' 하나면 설정 파일 자리(~/.claude/) 기준 상대 경로가 된다 (공식 문서)."""
    posix = home.absolute().as_posix()
    # 윈도우 C:/Users/… → //c/Users/… · macOS · Linux /home/… → //home/…
    windows = posix[1:2] == ":"
    expected = f"//{posix[0].lower()}{posix[2:]}/**" if windows else f"/{posix}/**"
    assert plugin_perms.rules(home) == [f"Read({expected})", f"Edit({expected})"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/home/u/.devdash", "//home/u/.devdash/**"), ("/Users/u/.devdash", "//Users/u/.devdash/**")],
)
def test_macOS_Linux_경로도_루트_기준으로_쓴다(path, expected, monkeypatch):
    class Fake:
        def absolute(self):
            return self

        def as_posix(self):
            return path

    assert plugin_perms.rules(Fake()) == [f"Read({expected})", f"Edit({expected})"]


def test_없는_규칙만_더하고_다른_설정은_그대로_둔다(home, tmp_path):
    path = tmp_path / "settings.json"
    keep = {"hooks": {"Stop": []}, "permissions": {"allow": ["Bash(git *)"], "deny": ["Read(x)"]}}
    path.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    assert "넣었습니다" in plugin_perms.allow(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["hooks"] == {"Stop": []} and data["permissions"]["deny"] == ["Read(x)"]
    assert data["permissions"]["allow"] == ["Bash(git *)", *plugin_perms.rules(home)]
    assert json.loads((tmp_path / "settings.json.devdash-backup").read_text("utf-8")) == keep


def test_다시_해도_중복되지_않고_파일을_건드리지_않는다(home, tmp_path):
    path = tmp_path / "settings.json"
    plugin_perms.allow(path)
    before = path.stat().st_mtime_ns
    assert plugin_perms.allow(path) == "이미 허용되어 있습니다"
    assert path.stat().st_mtime_ns == before


def test_설정_파일이_없으면_만든다(home, tmp_path):
    path = tmp_path / "new" / "settings.json"
    plugin_perms.allow(path)
    assert json.loads(path.read_text("utf-8"))["permissions"]["allow"] == plugin_perms.rules(home)


@pytest.mark.parametrize("text", ["{ 망가진", "[1, 2]"])
def test_읽을_수_없는_설정은_고치지_않는다(home, tmp_path, text):
    path = tmp_path / "settings.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        plugin_perms.allow(path)
    assert path.read_text(encoding="utf-8") == text


def test_BOM_이_붙은_설정도_읽고_줄바꿈은_LF_로_쓴다(home, tmp_path):
    """PowerShell 5.1 로 고친 설정 파일에는 BOM 이 붙는다."""
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"model": "opus"}')
    plugin_perms.allow(path)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw
    assert json.loads(raw)["model"] == "opus"


@pytest.mark.parametrize("perms", [None, "x", {"allow": "Read(x)"}])
def test_권한_모양이_다르면_고치지_않는다(home, tmp_path, perms):
    path = tmp_path / "settings.json"
    text = json.dumps({"permissions": perms})
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        plugin_perms.allow(path)
    assert path.read_text(encoding="utf-8") == text


def test_백업은_처음_원본만_남긴다(home, tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    plugin_perms.allow(path)
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path / "other"))
    plugin_perms.allow(path)
    assert (tmp_path / "settings.json.devdash-backup").read_text(encoding="utf-8") == "{}"
    assert not (tmp_path / "settings.json.devdash-tmp").exists()


def test_규칙을_빼면_비게_된_칸까지_치운다(home, tmp_path):
    path = tmp_path / "settings.json"
    before = {"model": "opus"}
    path.write_text(json.dumps(before), encoding="utf-8")
    plugin_perms.allow(path)
    assert plugin_perms.has_rules(path)
    assert plugin_perms.disallow(path) == "허용 규칙을 뺐습니다"
    assert json.loads(path.read_text("utf-8")) == before
    assert not plugin_perms.has_rules(path)


def test_규칙을_빼도_다른_허용_규칙은_남긴다(home, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"permissions": {"allow": ["Bash(git *)"]}}), encoding="utf-8")
    plugin_perms.allow(path)
    plugin_perms.disallow(path)
    assert json.loads(path.read_text("utf-8")) == {"permissions": {"allow": ["Bash(git *)"]}}


def test_뺄_규칙이_없으면_파일을_건드리지_않는다(home, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"permissions": "이상한 모양"}', encoding="utf-8")
    assert plugin_perms.disallow(path) == "허용 규칙이 없습니다"
    assert path.read_text("utf-8") == '{"permissions": "이상한 모양"}'
    assert plugin_perms.disallow(tmp_path / "없음.json") == "허용 규칙이 없습니다"


def test_설정_위치는_CLAUDE_CONFIG_DIR_을_따른다(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert plugin_perms.settings_path() == tmp_path / "cfg" / "settings.json"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert plugin_perms.settings_path() == Path.home() / ".claude" / "settings.json"
