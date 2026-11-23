# Dev Dashboard

원격 서버 · Claude Code 세션 · 이 PC 메모리를 한 화면에서 보고, 프로젝트마다 Claude 가 한 작업을 따라가는 로컬 대시보드입니다.
([English summary](#english-summary))

- **모니터링 탭**
  - 원격 서버(SSH): 자원 · 컨테이너
  - Claude Code 백그라운드 세션: 그룹 · 끌어 놓기 · 새 세션 · 연결/해제 · 삭제(30일 안에 복원)
  - 이 PC 메모리
- **프로젝트 탭**
  - 등록한 저장소의 작업(할 일) · 파일 · git · 세션 대화
  - 작업별 · 대화 차례별로 바뀐 파일 비교. 함께 설치되는 Claude Code 플러그인(`devdash`)이 기록합니다

## 요구 사항

- Windows · Linux · macOS
- Python 3.11 이상, Node.js(LTS), Git
- Claude Code (2.1.328 에서 시험) — 세션 칸과 작업 기록 플러그인에 필요합니다
- Windows 는 Git for Windows 가 있어야 합니다 (플러그인 훅이 `sh` 로 돕니다)

## 설치

```sh
git clone https://github.com/sebalnakji/Dev-Dashboard.git
cd Dev-Dashboard
./setup.sh        # Windows: setup.bat
```

setup 은 가상환경 · 화면 빌드 · 플러그인 설치를 합니다. Claude Code 설정을 바꾸기 전에 무엇을 바꾸는지 보여 주고 묻습니다.
작업 기록을 켤 프로젝트에서는 Claude 에게 `/devdash:init` 을 입력하세요.

## 실행

```sh
./start.sh        # Windows: start.bat — 뒤에서 띄우고 브라우저를 연다 (http://127.0.0.1:8770)
./stop.sh         # Windows: stop.bat
```

대시보드는 이 PC 안(127.0.0.1)에서만 열립니다.

## 설정

설정 · 기록은 모두 `~/.devdash` 에 둡니다 (저장소 밖).
모니터링할 서버는 `~/.devdash/servers.yaml` 에 적습니다. 예시는 [config/servers.example.yaml](config/servers.example.yaml) 에 있고, 없으면 서버 0대로 뜹니다.

## 알아 둘 것

- **"시험하지 않은 Claude Code 버전" 안내가 뜨면** 대시보드 폴더의 `check-claude.bat` 을 실행하세요 (macOS · Linux: `./check-claude.sh`). 시험 세션을 띄워 세션 관리가 그 버전에서 되는지 확인합니다 (몇 분 걸림).
- **대화 보존 기간**: Claude Code 는 대화 기록을 기본 30일 뒤 지웁니다. 지워진 세션은 이어 열 수 없습니다. 더 오래 두려면 `~/.claude/settings.json` 의 `cleanupPeriodDays` 를 늘리세요.
- **세션 이름**은 만들 때(`claude --bg --name …` 또는 대시보드의 [+] 새 세션) 붙여야 유지됩니다. 나중에 `/rename` 한 이름은 세션이 다시 뜰 때 사라질 수 있습니다.

## 바깥으로 나가는 것

- **AI 요약**: [git] 보기에서 [AI 요약 만들기]를 누를 때만, 그 커밋의 diff 를 `claude -p` 로 Anthropic 에 보냅니다.
- **git fetch**: [git] 보기를 열어 둔 프로젝트는 1분마다 원격에서 새 커밋을 받아 옵니다.
- **서버 모니터링**: 설정한 서버에만 SSH 로 접속합니다.

## 지우기

```sh
.venv/bin/python -m devdash uninstall          # Windows: .venv\Scripts\python -m devdash uninstall
```

setup 이 바꾼 Claude Code 설정(플러그인 · 권한 규칙)을 되돌립니다. 그 뒤 저장소 폴더와 `~/.devdash` 를 지우면 됩니다.

## 라이선스

MIT

---

## English summary

A local dashboard (UI in Korean) that shows remote servers, Claude Code background sessions and this PC's memory on one screen,
plus a per-project view of what Claude worked on: tasks, files, git history, session chats and before/after diffs of the files each task
or turn changed (recorded by the bundled Claude Code plugin `devdash`).

- Runs on Windows, Linux and macOS. Needs Python 3.11+, Node.js, Git and Claude Code (tested on 2.1.328). On Windows, Git for Windows is required.
- Install: `./setup.sh` (Windows: `setup.bat`). Run: `./start.sh` / `./stop.sh` (Windows: `start.bat` / `stop.bat`). Opens at http://127.0.0.1:8770, local only.
- State lives in `~/.devdash`. Servers go in `~/.devdash/servers.yaml` (see `config/servers.example.yaml`).
- Outbound traffic: AI commit summaries send the diff to Anthropic only when you click; projects with the git view open run `git fetch` every minute.
- Uninstall: `python -m devdash uninstall` from `.venv`, then delete the folder and `~/.devdash`.
- License: MIT.
