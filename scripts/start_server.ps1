# Minervini 스크리너 웹서버 런처.
# Tailscale 퍼널(minervini... → 127.0.0.1:8011)이 가리키는 8011에 uvicorn을 띄운다.
# 관리자 권한 불필요.
#
#   인자 없이 : 8011이 비어 있으면 띄우고, 이미 떠 있으면 아무것도 안 한다.
#               (로그인 시 시작프로그램의 minervini-server.vbs가 이 경로로 호출한다)
#   -Restart  : 8011을 점유한 프로세스를 정리한 뒤 새로 띄운다. 서버 파이썬 코드를
#               바꿨을 때 반영하려면 이 스위치가 필요하다(인자 없이는 갱신되지 않는다).
param([switch]$Restart)

$ErrorActionPreference = 'SilentlyContinue'

$proj = 'C:\Users\Liam\Desktop\minervini-screener'
$py   = "$proj\venv\Scripts\python.exe"
$port = 8011

function Get-Listener {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
}

if (Get-Listener) {
    if (-not $Restart) { exit 0 }   # 중복 실행 방지 (자동시작 경로)

    # 리스닝 소켓은 venv 런처가 띄운 '자식' 인터프리터가 갖는다. 자식만 종료하면 런처
    # 스텁이 고아로 남아 재시작마다 쌓이므로, 이 프로젝트 venv의 부모까지 함께 정리한다.
    foreach ($procId in @((Get-Listener).OwningProcess | Select-Object -Unique)) {
        $parentId = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId").ParentProcessId
        Stop-Process -Id $procId -Force
        if ($parentId) {
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$parentId"
            if ($parent.ExecutablePath -like "$proj\venv\*") { Stop-Process -Id $parentId -Force }
        }
    }

    # 포트가 풀릴 때까지 최대 5초 대기
    for ($i = 0; $i -lt 20 -and (Get-Listener); $i++) { Start-Sleep -Milliseconds 250 }
}

$env:PYTHONUTF8 = '1'
$env:ENABLE_SCHEDULER = 'false'   # 데이터 갱신은 별도 예약작업이 담당

Start-Process -FilePath $py `
    -ArgumentList '-m','uvicorn','main:app','--host','0.0.0.0','--port',"$port",'--log-level','warning' `
    -WorkingDirectory $proj `
    -WindowStyle Hidden `
    -RedirectStandardOutput "$proj\server-8011.log" `
    -RedirectStandardError  "$proj\server-8011.err.log"

# -Restart는 대화형 사용이므로 기동을 확인하고 결과를 알린다(자동시작 경로는 조용히 끝난다).
if ($Restart) {
    for ($i = 0; $i -lt 40 -and -not (Get-Listener); $i++) { Start-Sleep -Milliseconds 250 }
    $listener = Get-Listener
    if ($listener) { Write-Output "8011 재시작 완료 (PID $($listener.OwningProcess))" }
    else { Write-Output "8011 기동 실패 — $proj\server-8011.err.log 확인" }
}
