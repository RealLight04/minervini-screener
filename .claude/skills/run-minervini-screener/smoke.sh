#!/usr/bin/env bash
# Build, launch, and smoke-test the Minervini Stock Screener FastAPI app.
# Run from the repo root: bash .claude/skills/run-minervini-screener/smoke.sh
set -e

PORT="${PORT:-18010}"  # 8010·8011은 Tailscale 퍼널이 공개하는 포트 — 기본값으로 쓰지 않는다
BASE="http://localhost:${PORT}"
export PYTHONUTF8=1   # requirements.txt has Korean comments; on a non-UTF8-locale Windows
                       # (e.g. Korean codepage 949), pip's auto_decode falls back to cp949
                       # and crashes reading the file without this.

# --- venv + deps (idempotent) -------------------------------------------
if [ ! -f venv/Scripts/python.exe ] && [ ! -f venv/bin/python ]; then
  python -m venv venv
fi
if [ -f venv/Scripts/python.exe ]; then
  PY=venv/Scripts/python.exe   # Windows
else
  PY=venv/bin/python           # Linux/macOS
fi
"$PY" -m pip install -q -r requirements.txt

# --- stop only whatever is currently bound to $PORT (NOT every venv process) -------
# Used to match on the venv path (ps aux can't show argv here, so grepping for
# "uvicorn main:app" doesn't work) and kill by WINPID. That also matched the LIVE
# production server — scripts/start_server.ps1 runs uvicorn from this exact same
# venv, on port 8011. Default PORT=8010 looked like a different, safe port, but the
# path-based grep killed the live public site anyway before launching the test
# instance. Match by the port we're about to (re)use instead, via netstat — this
# only ever stops a stale instance already sitting on that port.
listening_pid=$(netstat -ano 2>/dev/null | awk -v port=":$PORT" '$1=="TCP" && $2 ~ (port "$") && $4=="LISTENING" {print $5}' | head -1)
if [ -n "$listening_pid" ]; then
  taskkill //F //PID "$listening_pid" > /dev/null 2>&1 || true
fi
sleep 1

# --- launch in background -------------------------------------------------
ENABLE_SCHEDULER=false "$PY" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --log-level warning > server.log 2>&1 &
echo "launched uvicorn (background), logs -> server.log"

# --- wait for readiness ----------------------------------------------------
for i in $(seq 1 30); do
  curl -sf "$BASE/api/stats" > /dev/null 2>&1 && break
  sleep 1
done

# --- smoke checks ----------------------------------------------------------
echo "--- /api/stats ---"
curl -s "$BASE/api/stats"; echo

echo "--- / (index) ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" "$BASE/"

echo "--- /stock/AMD (detail page) ---"
curl -s -o /dev/null -w "HTTP %{http_code}\n" "$BASE/stock/AMD"

echo "--- /api/chart/AMD (chart JSON) ---"
curl -s "$BASE/api/chart/AMD" | head -c 200; echo

echo ""
echo "Server is up at $BASE"
echo "Stop it with:"
echo "  netstat -ano | awk '\$2 ~ /:${PORT}\$/ && \$4==\"LISTENING\" {print \$5}' | xargs -I{} taskkill //F //PID {}"
