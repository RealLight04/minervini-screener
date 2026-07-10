#!/usr/bin/env bash
# Build, launch, and smoke-test the Minervini Stock Screener FastAPI app.
# Run from the repo root: bash .claude/skills/run-minervini-screener/smoke.sh
set -e

PORT="${PORT:-8010}"
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

# --- stop any previous instance of THIS project's venv (not other python procs) ----
# `pkill` does not exist in Git Bash on Windows, and the venv's python.exe shows up
# in `ps aux` without its args (so you can't grep for "uvicorn main:app" either) —
# match on the venv path instead, then kill by the WINPID column (4th field), which
# is the real Windows PID `taskkill` needs (the bash $! PID is a wrapper, not this).
for winpid in $(ps aux 2>/dev/null | grep "[m]inervini-screener/venv" | awk '{print $4}'); do
  taskkill //F //PID "$winpid" > /dev/null 2>&1 || true
done
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
echo '  for winpid in $(ps aux | grep "[m]inervini-screener/venv" | awk "{print \$4}"); do taskkill //F //PID "$winpid"; done'
