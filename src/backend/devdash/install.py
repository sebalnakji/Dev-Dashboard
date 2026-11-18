"""처음 설치(setup)와 되돌리기(uninstall).

가상환경 만들기와 이 패키지 설치는 껍데기(setup.bat · setup.sh)가 하고, 그 뒤는 여기서 한다.

    devdash setup       화면 빌드 · 빈 서버 설정 · (묻고) Claude Code 플러그인 설치
    devdash uninstall   (묻고) setup 이 바꾼 Claude Code 설정 세 가지를 되돌린다

Claude Code 의 전역 설정은 사람이 매일 쓰는 것이라, 바꾸기 전에 무엇을 바꾸는지 보여 주고 묻는다.
바꾸는 것은 우리 이름이 붙은 세 가지뿐이다: 마켓플레이스 `dev-dashboard`, 플러그인
`devdash@dev-dashboard`, 작업 기록 폴더(~/.devdash) 허용 규칙 두 줄. uninstall 은 이 셋을 지우고,
지우고 나서 빈 채로 남는 칸까지 치워 setup 전과 같은 설정으로 돌린다.

Claude 설정 폴더는 `CLAUDE_CONFIG_DIR` 을 따른다 — claude 명령도 이 변수를 따르므로, 시험할 때는
이 변수로 시험용 폴더를 주면 실제 설정을 건드리지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from devdash import plugin_perms
from devdash.home import REPO, home, servers_path
from devdash.local import run_bounded

MARKETPLACE = "dev-dashboard"
PLUGIN = f"devdash@{MARKETPLACE}"
FRONTEND = REPO / "src" / "frontend"
CLAUDE_TIMEOUT = 180.0
"""초. 플러그인 설치는 파일 복사라 보통 몇 초지만, 처음 실행하는 claude 는 느릴 수 있다."""

SERVERS_TEMPLATE = """\
# 모니터링할 서버의 접속 설정. 비어 있으면 서버 칸 없이 세션 · 메모리 칸만 쓴다.
# 쓰는 법은 저장소의 config/servers.example.yaml 을 본다.
servers: []
"""

Ask = Callable[[str], bool]


def ask_yes(question: str) -> bool:
    """[y/N] 로 묻는다. 입력이 없거나(파이프가 닫힘) y 가 아니면 아니오."""
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        print()
        return False


# --- claude 명령 ---------------------------------------------------------------


def _claude(*args: str) -> tuple[int, str]:
    """claude 명령을 돌려 (종료 코드, 출력)을 돌려준다. 시간이 넘으면 (-1, 안내)."""
    exe = shutil.which("claude")
    if not exe:
        return -1, "claude 명령을 찾지 못했습니다"
    try:
        result = run_bounded([exe, *args], timeout=CLAUDE_TIMEOUT)
    except OSError as exc:
        return -1, f"claude 를 실행하지 못했습니다: {exc}"
    if result is None:
        return -1, f"claude {' '.join(args)} 가 {CLAUDE_TIMEOUT:.0f}초 안에 끝나지 않았습니다"
    code, out, err = result
    return code, (out + err).strip()


def _claude_json(*args: str) -> list:
    code, out = _claude(*args, "--json")
    if code != 0:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def registered_marketplace() -> str | None:
    """마켓플레이스 dev-dashboard 가 가리키는 경로. 없으면 None."""
    for m in _claude_json("plugin", "marketplace", "list"):
        if isinstance(m, dict) and m.get("name") == MARKETPLACE:
            return str(m.get("path") or m.get("installLocation") or "")
    return None


def plugin_installed() -> bool:
    return any(
        isinstance(p, dict) and p.get("id") == PLUGIN for p in _claude_json("plugin", "list")
    )


def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


# --- setup ---------------------------------------------------------------------


def setup(ask: Ask = ask_yes) -> int:
    todo: list[str] = []

    _step("화면을 설치하고 빌드합니다")
    if not build_frontend(clean_install=True):
        return 1

    _step("서버 설정을 확인합니다")
    path = servers_path()
    if path.exists():
        print(f"  있음: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SERVERS_TEMPLATE, encoding="utf-8")
        print(f"  만듦: {path} (서버 0대)")
        todo.append(
            f"모니터링할 서버가 있으면 {path} 에 적으세요 (예시: config/servers.example.yaml)."
        )

    if shutil.which("claude"):
        _step("Claude Code 작업 기록 플러그인(devdash)")
        todo += setup_claude(ask)
    else:
        todo.append(
            "claude 명령을 찾지 못했습니다. Claude Code 를 설치해야 세션 칸과 작업 기록 플러그인이"
            " 동작합니다 (서버 · 메모리 칸은 없어도 됩니다). 설치한 뒤 setup 을 다시 실행하세요."
        )

    print("\n설치를 마쳤습니다.")
    if todo:
        print("\n남은 일:")
        for line in todo:
            print(f"  - {line}")
    print("\n실행은 start 입니다.\n")
    return 0


def setup_claude(ask: Ask) -> list[str]:
    """Claude Code 전역 설정 세 가지를 보여 주고, 승낙하면 바꾼다. 남은 일 목록을 돌려준다."""
    current = registered_marketplace()
    settings = plugin_perms.settings_path()
    changes = []
    if current is None:
        changes.append(f"마켓플레이스 등록: {MARKETPLACE} → {REPO}")
    elif not _same_path(current, REPO):
        changes.append(f"마켓플레이스 경로 바꿈: {MARKETPLACE}  {current} → {REPO}")
    if not plugin_installed():
        changes.append(f"플러그인 설치: {PLUGIN} (사용자 범위)")
    if not plugin_perms.has_rules(settings):
        rules = ", ".join(plugin_perms.rules(home()))
        changes.append(f"허용 규칙 추가 ({settings}): {rules}")

    if not changes:
        print("  이미 설치되어 있습니다. 고친 훅 · 스킬이 반영되게 갱신합니다.")
        _claude("plugin", "marketplace", "update", MARKETPLACE)
        _claude("plugin", "update", PLUGIN)
        return ["작업 기록을 켤 프로젝트에서 Claude 에게 /devdash:init 을 입력하세요."]

    print("  Claude Code 설정을 다음과 같이 바꿉니다 (되돌리기: uninstall):")
    for line in changes:
        print(f"    - {line}")
    print(
        "  허용 규칙은 작업 기록 파일을 쓸 때마다 묻지 않게 합니다 (백그라운드 세션이 멈추지 않게)."
    )
    if not ask("  바꿀까요?"):
        return [
            "Claude Code 설정을 바꾸지 않았습니다."
            " 작업 기록 플러그인을 쓰려면 setup 을 다시 실행하세요."
        ]

    todo = []
    for args in (
        ("plugin", "marketplace", "add", str(REPO)),
        ("plugin", "install", PLUGIN, "--scope", "user"),
        # 다시 설치할 때(코드를 새로 받은 뒤) 고친 훅 · 스킬이 반영되게. 처음이면 할 일이 없다
        ("plugin", "marketplace", "update", MARKETPLACE),
        ("plugin", "update", PLUGIN),
    ):
        code, out = _claude(*args)
        if code != 0:
            todo.append(f"'claude {' '.join(args)}' 실패: {out.splitlines()[-1] if out else code}")
    if plugin_installed():
        print(f"  설치됨: {PLUGIN} (새로 여는 세션부터 적용)")
    else:
        todo.append(
            f"플러그인 설치를 확인하지 못했습니다. 'claude plugin list' 로 {PLUGIN} 를 확인하세요."
        )
    try:
        print(f"  {plugin_perms.allow(settings)}")
    except (OSError, ValueError) as exc:
        todo.append(
            f"허용 규칙을 넣지 못했습니다 ({exc}). 그대로 두면 기록할 때마다 허락을 묻습니다."
        )

    if sys.platform == "win32" and not _py_launcher_ok():
        todo.append(
            "작업 기록 플러그인의 훅은 'py -3' 으로 파이썬 3.11 이상을 부릅니다."
            " python.org 설치본(py 런처 포함)을 설치하세요."
        )
    todo.append("작업 기록을 켤 프로젝트에서 Claude 에게 /devdash:init 을 입력하세요.")
    return todo


def _py_launcher_ok() -> bool:
    exe = shutil.which("py")
    if not exe:
        return False
    try:
        result = run_bounded(
            [exe, "-3", "-c", "import sys; print(sys.version_info >= (3, 11))"], timeout=30
        )
    except OSError:
        return False
    return result is not None and result[1].strip() == "True"


def build_frontend(clean_install: bool) -> bool:
    """화면 패키지를 받고(clean_install 이면 package-lock 그대로) 빌드한다.
    실패하면 이유를 알리고 False."""
    npm = shutil.which("npm")
    if not npm:
        print("Node.js 가 필요합니다. https://nodejs.org 에서 LTS 를 설치한 뒤 다시 실행하세요.")
        return False
    steps = [[npm, "ci", "--no-audit", "--no-fund"]] if clean_install else []
    steps.append([npm, "run", "build"])
    for args in steps:
        # 진행 상황이 보이게 출력은 그대로 흘려보낸다
        if subprocess.run(args, cwd=FRONTEND).returncode != 0:
            print(f"화면 준비에 실패했습니다: {' '.join(args[1:])}")
            return False
    return True


# --- uninstall -----------------------------------------------------------------


def uninstall(ask: Ask = ask_yes) -> int:
    settings = plugin_perms.settings_path()
    has_claude = shutil.which("claude") is not None
    current = registered_marketplace() if has_claude else None
    installed = plugin_installed() if has_claude else False

    removals = []
    if installed:
        removals.append(f"플러그인 제거: {PLUGIN}")
    if current is not None:
        removals.append(f"마켓플레이스 제거: {MARKETPLACE} ({current})")
    if plugin_perms.has_rules(settings):
        removals.append(f"허용 규칙 제거 ({settings}): {', '.join(plugin_perms.rules(home()))}")

    if not removals:
        print("Claude Code 설정에서 지울 것이 없습니다.")
    else:
        print("Claude Code 설정에서 다음을 지웁니다:")
        for line in removals:
            print(f"  - {line}")
        if not ask("지울까요?"):
            print("아무것도 바꾸지 않았습니다.")
            return 0
        failed = False
        if installed:
            failed |= _report(_claude("plugin", "uninstall", PLUGIN, "--scope", "user"))
        if current is not None:
            failed |= _report(_claude("plugin", "marketplace", "remove", MARKETPLACE))
        try:
            print(f"  {plugin_perms.disallow(settings)}")
            drop_empty_plugin_keys(settings)
        except (OSError, ValueError) as exc:
            print(f"  설정 파일을 고치지 못했습니다: {exc}")
            failed = True
        if failed:
            return 1

    print("\n지우지 않고 남겨 둔 것 (필요 없으면 직접 지우세요):")
    print(f"  - {home()}  작업 기록 · 서버 설정(servers.yaml) · 로그")
    print(f"  - {REPO / '.venv'}  파이썬 가상환경")
    backup = settings.with_name(settings.name + ".devdash-backup")
    if backup.exists():
        print(f"  - {backup}  setup 이 처음 고치기 전의 Claude 설정 백업")
    return 0


def _report(result: tuple[int, str]) -> bool:
    """claude 명령 결과를 한 줄로 알리고, 실패했으면 True."""
    code, out = result
    last = out.splitlines()[-1] if out else ""
    print(f"  {last}" if code == 0 else f"  실패: {last or code}")
    return code != 0


def drop_empty_plugin_keys(path: Path) -> None:
    """claude 의 제거 명령이 남기는 빈 칸(enabledPlugins: {} · extraKnownMarketplaces: {})을 치운다.
    다른 플러그인이 남아 있으면 비어 있지 않으므로 그대로 둔다."""
    if not path.exists():
        return
    settings = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    if not isinstance(settings, dict):
        return
    empty = [k for k in ("enabledPlugins", "extraKnownMarketplaces") if settings.get(k) == {}]
    if not empty:
        return
    for key in empty:
        del settings[key]
    plugin_perms.write_settings(path, settings)


def _step(text: str) -> None:
    print(f"\n> {text}")
