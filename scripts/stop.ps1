# ============================================================
#  Dev-Dashboard 종료
#
#  창을 직접 닫아도 되지만 npm 은 자식 프로세스를 남기는 일이
#  있어서, 포트를 쥐고 있는 쪽을 찾아 끄는 편이 확실하다.
# ============================================================

foreach ($port in 8770, 5173) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
}
