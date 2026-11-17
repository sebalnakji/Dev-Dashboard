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


def test_윈도우_경로는_권한_규칙_표기로_바꾼다(home):
    posix = home.absolute().as_posix()
    expected = f"//{posix[0].lower()}{posix[2:]}/**"
    assert plugin_perms.rules(home) == [f"Read({expected})", f"Edit({expected})"]


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


def test_명령으로_불러도_로그_없이_결과_한_줄만_낸다(home, tmp_path, monkeypatch, capsys):
    from devdash.__main__ import main

    monkeypatch.setattr(plugin_perms, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(plugin_perms.allow, "__defaults__", (tmp_path / "settings.json",))
    monkeypatch.chdir(tmp_path)
    assert main(["plugin", "allow"]) == 0
    assert "허용 규칙을 넣었습니다" in capsys.readouterr().out
    assert not (tmp_path / "logs").exists()
