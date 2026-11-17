# ============================================================
#  Dev-Dashboard 실행
#
#  백엔드(FastAPI) 하나가 API 와 화면을 같은 주소(8770)에서 함께 서빙한다.
#  예전엔 Vite 개발 서버(5173)를 따로 띄웠는데, 세션 종료·좀비 프로세스로
#  자꾸 죽어 화면이 끊겼다. 이제 빌드본(dist)을 백엔드가 직접 준다.
#  개발할 때만 src/frontend 에서 vite dev 서버를 쓴다.
# ============================================================

$root   = Split-Path -Parent $PSScriptRoot
$port   = 8770
$python = Join-Path $root '.venv\Scripts\python.exe'

function Show-Error([string]$Message) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($Message, 'Dev-Dashboard', 'OK', 'Error') | Out-Null
}

function Stop-Port([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}

function Wait-Port([int]$Port, [int]$TimeoutSeconds = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { return $true }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

if (-not (Test-Path $python)) {
    Show-Error "아직 설치하지 않았습니다. setup.bat 을 먼저 실행하세요.`n`n(파이썬 가상환경이 없습니다: $python)"
    exit 1
}

# 화면 빌드본이 없으면(처음이거나 정리됐으면) 한 번 빌드한다. node 가 필요하다.
$indexHtml = Join-Path $root 'src\frontend\dist\index.html'
if (-not (Test-Path $indexHtml)) {
    Push-Location (Join-Path $root 'src\frontend')
    & npm.cmd run build 2>&1 | Out-Null
    Pop-Location
    if (-not (Test-Path $indexHtml)) {
        Show-Error "화면 빌드본이 없고 빌드도 실패했습니다. setup.bat 을 다시 실행하세요."
        exit 1
    }
}

# 지난번에 죽다 만 백엔드가 포트를 쥐고 있으면 새 것이 못 뜬다. 먼저 치운다.
Stop-Port $port

Start-Process -FilePath $python `
              -ArgumentList '-m', 'devdash', 'serve' `
              -WorkingDirectory $root `
              -WindowStyle Minimized

if (-not (Wait-Port $port)) {
    Show-Error "백엔드가 뜨지 않았습니다. 포트 $port 를 확인하세요."
    exit 1
}

Start-Process "http://127.0.0.1:$port/"
