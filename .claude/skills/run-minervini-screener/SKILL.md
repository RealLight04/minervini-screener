---
name: run-minervini-screener
description: Build, run, and drive the Minervini Stock Screener FastAPI app locally. Use when asked to start/run minervini-screener, test its API/pages, or verify a local change before pushing (Render auto-deploys off main).
---

FastAPI + SQLite stock screener (Mark Minervini SEPA/VCP methodology, US + KOSPI/KOSDAQ).
Driven via `.claude/skills/run-minervini-screener/smoke.sh` — it builds the venv, launches
uvicorn in the background, and curl-checks the key pages/endpoints. No GUI beyond
server-rendered HTML, so curl is the harness (no browser needed).

All paths below are relative to the repo root (`minervini-screener/`).

## Prerequisites

- Python 3.11+ (tested with 3.11.8 on native Windows via Git Bash — no WSL needed).
- No system packages beyond Python + git. `requirements.txt` covers everything else.
- No `.env` / API keys required for a basic local run — `config.py` defaults
  `DATABASE_URL`, `ENABLE_SCHEDULER`, etc. to safe values. `DART_API_KEY`,
  `ALPHAVANTAGE_API_KEY`, `GMAIL_*` are only for optional data-collection/email
  features, not for serving the screener pages.

## Setup

The repo ships a committed `screener.db` snapshot (~35MB, real data), so no data
collection step is needed just to run it — `smoke.sh` handles venv + deps:

```bash
bash .claude/skills/run-minervini-screener/smoke.sh
```

## Run (agent path)

The smoke script is idempotent — safe to re-run, it stops any prior instance first:

```bash
bash .claude/skills/run-minervini-screener/smoke.sh
```

What it does, in order: create `venv/` if missing → `pip install -r requirements.txt`
→ stop any previous instance of this project's server → launch
`uvicorn main:app --host 0.0.0.0 --port 8010` in the background (logs → `server.log`)
→ poll `/api/stats` until ready → curl `/`, `/stock/AMD`, `/api/chart/AMD`.

Override the port with `PORT=8020 bash .claude/skills/run-minervini-screener/smoke.sh`.

Expected output ends with:
```
--- /api/stats ---
{"date":"2026-07-09","total":812,"technical_pass":109,"final_pass":8}
--- / (index) ---
HTTP 200
--- /stock/AMD (detail page) ---
HTTP 200
--- /api/chart/AMD (chart JSON) ---
{"ticker":"AMD","name":"Advanced Micro Devices",...}
```

### Manual endpoint checks

| Endpoint | What it is |
|---|---|
| `GET /` | Buy/sell candidate list, market breadth, leading sectors (`?market=US\|KOSPI\|KOSDAQ`) |
| `GET /stock/{ticker}` | Stock detail: 8-point trend template, fundamentals, VCP, trade plan |
| `GET /api/stats` | `{date, total, technical_pass, final_pass}` — quick health/data-freshness check |
| `GET /api/chart/{ticker}` | OHLC + MA50/150/200 + pivot/stop, for the candlestick chart |
| `GET /api/screen-now` | Manually re-run the screener against the current DB (dev/test only) |
| `GET /vcp` | VCP registry: forming bases + recent breakouts |

### Stop

```bash
for winpid in $(ps aux | grep "[m]inervini-screener/venv" | awk '{print $4}'); do taskkill //F //PID "$winpid"; done
```

## Run (human path)

Open `http://localhost:8010/` in a browser once `smoke.sh` reports it's up.
The repo's own `run.sh` is WSL2-oriented (assumes `hostname -I`, port 8001) —
on native Windows, `smoke.sh` is the path that's actually verified to work.

## Test

No automated test suite in this repo — `smoke.sh` (endpoint checks) is the
verification path. For a full data refresh (not needed to just run the app):
`python scripts/daily_update.py` (~15-20 min, hits yfinance for all S&P 500).

---

## Gotchas

- **`pkill` does not exist in Git Bash on Windows.** The repo's own `HANDOFF.md`
  documents a `pkill -f "[u]vicorn main:app"` trick from a WSL2/Linux session —
  it silently no-ops here (`pkill: command not found`, swallowed by `2>/dev/null`),
  so the "stop" step looked like it worked but didn't. Use the `ps aux | grep ... |
  awk '{print $4}'` → `taskkill //F //PID` pattern in `smoke.sh` instead.
- **`ps` here doesn't show command-line args, only the exe path** — so you can't
  grep for `"uvicorn main:app"` either; match on the venv's path instead
  (`minervini-screener/venv`), with the bracket self-exclusion trick (`[m]inervini...`)
  so the grep process doesn't match its own argv.
- **Bash's `$!` after backgrounding is the wrong PID to kill.** It resolves to an
  MSYS wrapper process, not the actual `python.exe` — `taskkill`ing it leaves the
  real server running. Look up the real process via `ps aux | grep venv` and use
  the **4th column (WINPID)**, not `$!` and not the 1st column (MSYS PID).
- **`pip install -r requirements.txt` can crash with
  `UnicodeDecodeError: 'cp949' codec can't decode byte ...`** — `requirements.txt`
  has Korean comments, and on a non-UTF8-locale Windows, pip's encoding
  auto-detection falls back to the system codepage (cp949) and chokes on the
  UTF-8 bytes. Fix: `export PYTHONUTF8=1` before running pip (already in `smoke.sh`).
  This didn't reproduce every time — it depends on ambient locale — so don't
  assume a single successful run means it's fixed for everyone.
- **If you clone into a directory NOT named `minervini-screener`**, the stop
  pattern (which greps for that literal path segment) won't match — see
  Troubleshooting.
- **Git Bash mangles `curl -w` format strings that start with `/`** — e.g.
  `curl -w "/ -> %{http_code}\n"` can print garbage like `C:/Program Files/Git/ ->
  200/n` because MSYS auto-converts leading-`/` arguments to Windows paths. The
  HTTP code after `->` is still correct; it's cosmetic. `smoke.sh` avoids leading
  `/` in `-w` strings to sidestep this.
- **`scripts/collect_kr.py` / `scripts/local_refresh.py --mode kr` need
  `finance-datareader`, which is deliberately NOT in `requirements.txt`** (kept out
  to keep the Render deploy lean — Render only serves, never collects KR data).
  `smoke.sh`'s `pip install -r requirements.txt` alone will NOT install it, so a
  fresh local venv can serve pages fine but silently fail KR collection with
  `FinanceDataReader 미설치`. Fix: `venv/Scripts/python.exe -m pip install
  finance-datareader` once, same as GitHub Actions' `pip install -r
  requirements.txt finance-datareader` step does.
- **Windows Task Scheduler triggers need admin elevation to create, except plain
  calendar triggers (`/SC DAILY`/`WEEKLY`) for the current user.** `ONSTART` and
  `ONLOGON` triggers, and any `/RU SYSTEM` or `/RU <user> /RP <password>` task,
  both return `ERROR: Access is denied.` from a non-elevated shell — there's no
  way around this without either an elevated prompt or the user entering their
  password interactively (which an agent shouldn't do). The 3 local data-refresh
  tasks (`MinerviniRefreshKR`/`-US-EDT`/`-US-EST`, see HANDOFF.md) work unelevated
  because they're WEEKLY triggers under the current user; the boot-time web-server
  autostart task needs `ONSTART`/`ONLOGON` and could NOT be created this way —
  it's still a pending manual step (see HANDOFF.md).

## Troubleshooting

- **Re-running `smoke.sh` seems to "hang" waiting for readiness**: check
  `server.log` — a prior instance may still hold the port if the stop step
  didn't match (see the directory-name gotcha above). Find it manually:
  `ps aux | grep venv`, then `taskkill //F //PID <4th column>`.
- **`ModuleNotFoundError` after `pip install` appeared to succeed**: check
  `server.log` for a `UnicodeDecodeError` further up — if `pip install` crashed
  partway through, later packages never installed. Re-run with `PYTHONUTF8=1` set.
