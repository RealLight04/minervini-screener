#!/usr/bin/env bash
# Minervini 스크리너 실행 스크립트 (WSL2 대응)
set -e

PORT="${PORT:-8001}"   # 8000은 다른 앱이 사용 중이라 8001 사용
cd "$(dirname "$0")"

# 기존 서버 종료 (포트 충돌 방지)
pkill -f "uvicorn main:app" 2>/dev/null || true
sleep 1

# WSL2 IP 확인
WSL_IP=$(hostname -I | awk '{print $1}')

echo "────────────────────────────────────────────"
echo "  Minervini Stock Screener 시작"
echo "────────────────────────────────────────────"
echo "  Windows 브라우저에서 아래 주소로 접속하세요:"
echo ""
echo "    http://localhost:${PORT}"
echo "    http://${WSL_IP}:${PORT}   ← localhost가 안 되면 이 주소"
echo ""
echo "  종료: Ctrl+C"
echo "────────────────────────────────────────────"

# 0.0.0.0 바인딩 → Windows에서 WSL IP로 접근 가능
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT}"
