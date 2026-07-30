# Minervini 스크리너 웹서버 자동시작 런처 (로그인 시 시작프로그램에서 호출).
# Tailscale 퍼널(minervini... → 127.0.0.1:8011)이 가리키는 8011에 uvicorn을 띄운다.
# 관리자 권한 불필요. 이미 8011이 떠 있으면 중복 실행하지 않는다.
$ErrorActionPreference = 'SilentlyContinue'

$proj = 'C:\Users\Liam\Desktop\minervini-screener'
$py   = "$proj\venv\Scripts\python.exe"
$port = 8011

# 이미 리스닝 중이면 아무것도 안 함 (중복 방지)
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    exit 0
}

$env:PYTHONUTF8 = '1'
$env:ENABLE_SCHEDULER = 'false'   # 데이터 갱신은 별도 예약작업이 담당

Start-Process -FilePath $py `
    -ArgumentList '-m','uvicorn','main:app','--host','0.0.0.0','--port',"$port",'--log-level','warning' `
    -WorkingDirectory $proj `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$proj\server-8011.log" `
    -RedirectStandardError  "$proj\server-8011.err.log"
