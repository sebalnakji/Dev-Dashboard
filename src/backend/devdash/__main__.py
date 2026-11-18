"""명령줄 진입점.

devdash setup           처음 설치 (화면 빌드 · 빈 서버 설정 · 묻고 Claude Code 플러그인 설치)
devdash start           대시보드를 뒤에서 띄우고 브라우저를 연다
devdash stop            start 로 띄운 대시보드를 끈다
devdash uninstall       setup 이 바꾼 Claude Code 설정을 되돌린다 (묻고)
devdash list            설정된 서버 목록을 본다
devdash check           접속해서 지표를 한 번 수집해본다 (진단용)
devdash serve           웹 대시보드를 이 창에서 띄운다

설정 · 세션 목록 · 로그는 모두 ~/.devdash 에 있다 (DEVDASH_HOME 으로 바꿀 수 있다).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from devdash.cli import cmd_check, cmd_list, use_utf8_when_redirected
from devdash.launch import DEFAULT_PORT
from devdash.logging_setup import setup_logging


def cmd_serve(config_path: Path | None, host: str, port: int) -> int:
    import uvicorn

    from devdash.app import create_app
    from devdash.claude.agents import drop_claude_markers

    # 대시보드가 띄우는 claude 가 '하위 세션'으로 오인되지 않게 (agents.drop_claude_markers)
    drop_claude_markers()
    app = create_app(config_path)
    print(f"대시보드: http://{host}:{port}")
    # uvicorn 이 자체 로그 설정으로 우리 설정을 덮어쓰지 않게 한다
    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devdash", description=__doc__)
    parser.add_argument(
        "-c", "--config", type=Path, help="서버 설정 파일 경로. 기본 ~/.devdash/servers.yaml"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그 출력")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="처음 설치. 다시 실행해도 안전하다 (코드를 새로 받은 뒤 갱신)")
    start = sub.add_parser("start", help="대시보드를 뒤에서 띄우고 브라우저를 연다")
    start.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"포트. 기본 {DEFAULT_PORT}")
    sub.add_parser("stop", help="start 로 띄운 대시보드를 끈다")
    sub.add_parser("uninstall", help="setup 이 바꾼 Claude Code 설정을 되돌린다")

    sub.add_parser("list", help="설정된 서버 목록을 보여준다")

    check = sub.add_parser("check", help="서버에 접속해 지표를 한 번 수집해본다")
    check.add_argument(
        "server", nargs="*", help="확인할 서버 id. 생략하면 설정된 서버를 모두 확인한다"
    )
    check.add_argument("--interval", type=float, default=2.0, help="두 샘플 사이 간격(초). 기본 2")

    serve = sub.add_parser("serve", help="웹 대시보드를 띄운다")
    serve.add_argument("--host", default="127.0.0.1", help="바인딩 주소. 기본은 이 PC 안에서만")
    # 8000 은 다른 개발 서버가 잡고 있는 경우가 많아 덜 붐비는 번호를 쓴다.
    # 바꿀 때는 src/frontend/vite.config.ts 의 프록시 대상도 같이 고쳐야 한다.
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"포트. 기본 {DEFAULT_PORT}")

    args = parser.parse_args(argv)
    use_utf8_when_redirected()
    if args.command in ("setup", "uninstall", "start", "stop"):
        # 사람이 콘솔에서 결과를 보는 명령이라 로그 파일을 만들지 않는다
        return _cmd_install(args)

    setup_logging(verbose=args.verbose)

    try:
        if args.command == "list":
            return cmd_list(args.config)
        if args.command == "check":
            # 서버별 접속 실패는 cmd_check 안에서 개별로 보고한다
            return cmd_check(args.config, args.server, args.interval)
        if args.command == "serve":
            return cmd_serve(args.config, args.host, args.port)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"오류: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    from devdash import install, launch

    try:
        if args.command == "setup":
            return install.setup()
        if args.command == "uninstall":
            return install.uninstall()
        if args.command == "start":
            return launch.start(args.port)
        return launch.stop()
    except KeyboardInterrupt:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
