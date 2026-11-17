# ============================================================
#  처음 한 번 설치 (setup.bat 이 부른다)
#
#  1) 파이썬 가상환경(.venv)을 만들고 백엔드를 설치한다
#  2) 화면 패키지를 설치하고 빌드한다 (node 필요)
#  3) config/ 설정 파일이 없으면 만들고, 채울 곳을 알려준다
#  4) Claude Code 작업 기록 플러그인(devdash)을 설치하고, 작업 기록 폴더(~/.devdash)를
#     묻지 않고 쓰게 사용자 설정(~/.claude/settings.json)에 허용 규칙 두 줄을 넣는다
#
#  다시 실행해도 안전하다. 코드를 새로 받은 뒤(git pull) 다시 돌리면 갱신된다.
#  윈도우만 지원한다 (ADR 0005).
# ============================================================

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

function Step([string]$Text) { Write-Host "`n▶ $Text" -ForegroundColor Cyan }
function Fail([string]$Text) { Write-Host "`n✖ $Text" -ForegroundColor Red; exit 1 }

# --- 1. 파이썬 ---
Step '파이썬 3.11 이상을 찾습니다'
# py 런처가 있으면 버전을 골라 쓰고, 없으면 PATH 의 python 을 쓴다.
# (스토어 바로가기 python.exe 는 실행만 되고 아무것도 안 하는 경우가 있어 버전을 직접 확인한다)
$exe = $null
$pre = @()
foreach ($cand in 'py -3', 'python') {
    $parts = $cand -split ' '
    $rest = @($parts | Select-Object -Skip 1)
    try {
        $ok = & $parts[0] @rest -c 'import sys; print(sys.version_info >= (3, 11))' 2>$null
        if ($ok -eq 'True') { $exe = $parts[0]; $pre = $rest; break }
    } catch { }
}
if (-not $exe) { Fail '파이썬 3.11 이상이 필요합니다. https://www.python.org 에서 설치한 뒤 다시 실행하세요.' }
Write-Host "  사용: $exe $pre"

if (-not (Test-Path $venvPython)) {
    Step '가상환경(.venv)을 만듭니다'
    & $exe @pre -m venv (Join-Path $root '.venv')
    if ($LASTEXITCODE -ne 0) { Fail '가상환경을 만들지 못했습니다.' }
}

Step '백엔드를 설치합니다'
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e (Join-Path $root 'src\backend')
if ($LASTEXITCODE -ne 0) { Fail '백엔드 설치에 실패했습니다.' }

# --- 2. 화면 ---
Step '화면을 설치하고 빌드합니다'
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    Fail 'Node.js 가 필요합니다. https://nodejs.org 에서 LTS 를 설치한 뒤 다시 실행하세요.'
}
Push-Location (Join-Path $root 'src\frontend')
try {
    # package-lock.json 과 똑같은 버전으로 받는다
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Fail '화면 패키지 설치에 실패했습니다.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { Fail '화면 빌드에 실패했습니다.' }
} finally {
    Pop-Location
}

# --- 3. 설정 ---
Step '설정 파일을 확인합니다'
$config = Join-Path $root 'config'
$todo = @()

$servers = Join-Path $config 'servers.yaml'
if (-not (Test-Path $servers)) {
    Copy-Item (Join-Path $config 'servers.example.yaml') $servers
    $todo += "config\servers.yaml 에 모니터링할 서버의 접속 정보를 적으세요 (예시를 복사해 두었습니다)."
}

$sessions = Join-Path $config 'sessions.json'
if (-not (Test-Path $sessions)) {
    # 빈 목록으로 시작한다. 세션은 실행 중인 것을 골라 명령으로 넣는다
    [IO.File]::WriteAllText($sessions, "{`n  `"sessions`": []`n}`n", (New-Object Text.UTF8Encoding $false))
    $todo += "Claude 세션은 실행 중인 것을 골라 넣습니다 (저장소 폴더에서):`n" +
             "      .venv\Scripts\python -m devdash claude list          ← 추가 후보 확인`n" +
             "      .venv\Scripts\python -m devdash claude add <번호 앞자리> --name 이름 --group 그룹"
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    $todo += "claude 명령을 찾지 못했습니다. Claude Code 를 설치해야 세션 칸이 동작합니다 (서버·메모리 칸은 없어도 됩니다)."
} else {
    # --- 4. Claude Code 작업 기록 플러그인 (ADR 0007 · 0008) ---
    Step 'Claude Code 작업 기록 플러그인(devdash)을 설치합니다'
    # claude 가 경고를 stderr 로 내도 설치를 멈추지 않게 이 구간만 느슨하게 둔다
    $ErrorActionPreference = 'Continue'

    # 훅은 'py -3' 으로 파이썬을 부른다 (Git Bash 의 python 은 스토어 바로가기라 동작하지 않는다)
    $pyOk = $false
    try { $pyOk = (& py -3 -c 'import sys; print(sys.version_info >= (3, 11))') -eq 'True' } catch { }
    if (-not $pyOk) {
        $todo += "작업 기록 플러그인의 훅은 'py -3' 으로 파이썬 3.11 이상을 부릅니다. python.org 설치본(py 런처 포함)을 설치하세요."
    }

    # 이 저장소가 마켓플레이스다 (.claude-plugin/marketplace.json). 이미 있으면 그대로 둔다
    & claude plugin marketplace add $root | Out-Null
    & claude plugin install devdash@dev-dashboard --scope user | Out-Null
    # 다시 실행(git pull 뒤)할 때 고친 훅 · 스킬이 반영되게. 처음 설치면 할 일이 없을 뿐이다
    & claude plugin marketplace update dev-dashboard | Out-Null
    & claude plugin update devdash@dev-dashboard | Out-Null
    $installed = $false
    try {
        $installed = @(& claude plugin list --json | Out-String | ConvertFrom-Json |
            ForEach-Object { $_ } | Where-Object { $_.id -eq 'devdash@dev-dashboard' -and $_.enabled }).Count -gt 0
    } catch { }
    if ($installed) {
        Write-Host '  설치됨: devdash@dev-dashboard (새로 여는 세션부터 적용)'
    } else {
        $todo += "작업 기록 플러그인 설치를 확인하지 못했습니다. 'claude plugin list' 로 devdash@dev-dashboard 를 확인하세요."
    }

    # 작업 기록 폴더(~/.devdash)는 저장소 밖이라 파일마다 허락을 묻는다. 백그라운드 세션이 멈추지 않게
    # 사용자 설정에 허용 규칙 두 줄을 넣는다 (플러그인은 권한 규칙을 스스로 실어 보낼 수 없다)
    & $venvPython -m devdash plugin allow
    if ($LASTEXITCODE -ne 0) {
        $todo += "작업 기록 폴더 허용 규칙을 넣지 못했습니다 (위 오류 참고). 그대로 두면 기록할 때마다 허락을 묻습니다."
    }
    $ErrorActionPreference = 'Stop'

    $todo += "작업 기록을 켤 프로젝트에서 Claude 에게 /devdash:init 을 입력하세요."
}

# --- 끝 ---
Write-Host "`n✔ 설치를 마쳤습니다." -ForegroundColor Green
if ($todo.Count -gt 0) {
    Write-Host "`n남은 일:" -ForegroundColor Yellow
    $todo | ForEach-Object { Write-Host "  - $_" }
}
Write-Host "`n실행은 start.bat 입니다.`n"
