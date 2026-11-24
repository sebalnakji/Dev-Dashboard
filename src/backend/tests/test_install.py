"""setup · uninstall 의 Claude Code 설정 부분 테스트.

claude 명령은 흉내 낸다 (등록 상태를 사전에 들고 명령에 따라 바꾼다). 설정 파일은 임시 CLAUDE_CONFIG_DIR.
실제 claude 로 하는 시험은 사람이 시험용 CLAUDE_CONFIG_DIR 에서 따로 돌린다.
"""

from __future__ import annotations

import json

import pytest

from devdash import install, plugin_perms


class FakeClaude:
    def __init__(self, marketplace: str | None = None, installed: bool = False):
        self.marketplace = marketplace
        self.installed = installed
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:3] == ("plugin", "marketplace", "list"):
            rows = [{"name": "dev-dashboard", "path": self.marketplace}] if self.marketplace else []
            return 0, json.dumps(rows)
        if args[:2] == ("plugin", "list"):
            return 0, json.dumps([{"id": install.PLUGIN}] if self.installed else [])
        if args[:3] == ("plugin", "marketplace", "add"):
            self.marketplace = args[3]
        elif args[:2] == ("plugin", "install"):
            self.installed = True
        elif args[:2] == ("plugin", "uninstall"):
            self.installed = False
        elif args[:3] == ("plugin", "marketplace", "remove"):
            self.marketplace = None
        return 0, "ok"

    def changed(self) -> list[tuple]:
        return [a for a in self.calls if "list" not in a]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVDASH_HOME", str(tmp_path / "devdash"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    fake = FakeClaude()
    monkeypatch.setattr(install, "_claude", fake)
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(install, "_py_launcher_ok", lambda: True)
    return {"settings": settings, "fake": fake}


def _answer(reply: bool, asked: list[str]):
    def ask(question: str) -> bool:
        asked.append(question)
        return reply

    return ask


def test_setup_은_묻고_거절하면_아무것도_바꾸지_않는다(env, capsys):
    before = env["settings"].read_text("utf-8")
    asked: list[str] = []
    install.setup_claude(_answer(False, asked))
    out = capsys.readouterr().out
    assert asked and "마켓플레이스 등록" in out and "플러그인 설치" in out and "허용 규칙" in out
    assert env["fake"].changed() == []
    assert env["settings"].read_text("utf-8") == before


def test_setup_은_마켓플레이스가_다른_폴더를_가리키면_바뀐다고_알린다(env, capsys):
    env["fake"].marketplace = "C:/옛/폴더"
    install.setup_claude(_answer(False, []))
    out = capsys.readouterr().out
    assert "경로 바꿈" in out and "C:/옛/폴더" in out


def test_setup_승낙_뒤_uninstall_하면_설정이_처음과_같다(env):
    before = json.loads(env["settings"].read_text("utf-8"))
    install.setup_claude(_answer(True, []))
    fake = env["fake"]
    assert fake.installed and fake.marketplace == str(install.REPO)
    assert plugin_perms.has_rules(env["settings"])

    # claude 의 제거 명령이 남기는 빈 칸을 흉내 낸다
    data = json.loads(env["settings"].read_text("utf-8"))
    data.update({"enabledPlugins": {}, "extraKnownMarketplaces": {}})
    env["settings"].write_text(json.dumps(data), encoding="utf-8")

    assert install.uninstall(_answer(True, [])) == 0
    assert not fake.installed and fake.marketplace is None
    assert json.loads(env["settings"].read_text("utf-8")) == before


def test_uninstall_은_묻고_거절하면_아무것도_지우지_않는다(env):
    install.setup_claude(_answer(True, []))
    after_setup = env["settings"].read_text("utf-8")
    env["fake"].calls.clear()
    assert install.uninstall(_answer(False, [])) == 0
    assert env["fake"].changed() == []
    assert env["settings"].read_text("utf-8") == after_setup


def test_uninstall_은_남겨_둔_것을_알린다(env, capsys):
    assert install.uninstall(_answer(True, [])) == 0
    out = capsys.readouterr().out
    assert "지울 것이 없습니다" in out
    assert str(install.home()) in out and ".venv" in out


def test_다른_플러그인이_남아_있으면_칸을_치우지_않는다(env):
    path = env["settings"]
    path.write_text(json.dumps({"enabledPlugins": {"x@y": True}, "extraKnownMarketplaces": {}}))
    install.drop_empty_plugin_keys(path)
    assert json.loads(path.read_text("utf-8")) == {"enabledPlugins": {"x@y": True}}


def test_setup_은_빈_서버_설정을_만들고_있는_것은_그대로_둔다(env, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "build_frontend", lambda clean_install: True)
    monkeypatch.setattr(install, "setup_claude", lambda ask: [])
    assert install.setup(ask=lambda q: False) == 0
    servers = tmp_path / "devdash" / "servers.yaml"
    assert "servers: []" in servers.read_text("utf-8")

    servers.write_text("servers: []  # 내가 고침\n", encoding="utf-8")
    install.setup(ask=lambda q: False)
    assert servers.read_text("utf-8") == "servers: []  # 내가 고침\n"


# --- 편의 설정: 바탕화면 바로가기 · 자동 호환 시험 ---


def test_바로가기는_물어서_만들고_다시_묻지_않고_uninstall_은_만든_것만_지운다(
    env, _desktop, capsys
):
    from devdash import prefs, shortcut

    hand_made = _desktop / "Dev Dashboard.bat"  # 사람이 손으로 만든 것 — 건드리면 안 된다
    hand_made.write_text("@echo off", encoding="utf-8")
    asked = []
    todo = install.setup_extras(lambda q: asked.append(q) or True)
    assert todo == [] and any("바로가기" in q for q in asked)
    made = shortcut.existing()
    assert made is not None and made.parent == _desktop and prefs.get("shortcut") == str(made)

    asked.clear()
    install.setup_extras(
        lambda q: asked.append(q) or True
    )  # 이미 있으면 · 이미 답했으면 묻지 않는다
    assert asked == [] and "있음" in capsys.readouterr().out

    shortcut.remove()
    assert not made.exists() and hand_made.exists() and prefs.get("shortcut") is None


def test_바탕화면에_같은_이름이_있으면_만들지_않는다(env, _desktop, capsys):
    from devdash import shortcut

    shortcut.target(_desktop).write_text("남의 것", encoding="utf-8")
    asked = []
    install.setup_extras(lambda q: asked.append(q) or True)
    assert not any("바로가기" in q for q in asked) and shortcut.existing() is None
    assert shortcut.target(_desktop).read_text(encoding="utf-8") == "남의 것"


def test_자동_호환_시험은_한_번_묻고_답을_적어_둔다(env):
    from devdash import compat_auto

    install.setup_extras(lambda q: "호환 시험" in q)
    assert compat_auto.enabled() is True
    asked = []
    install.setup_extras(lambda q: asked.append(q) or False)
    assert not any("호환 시험" in q for q in asked) and compat_auto.enabled() is True
