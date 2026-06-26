# 🔁 작업 인계 런북 (Minervini Stock Screener)

> 이 파일은 **새 세션에서 그대로 이어서 같은 작업을 재현**하기 위한 런북입니다.
> 마지막 갱신: 2026-06-26. 작업 디렉토리: `/home/pc100di/stock-screener`

---

## 0. 한 줄 요약

마크 미너비니 SEPA(Trend Template + 실적 + VCP) 방법론으로 **S&P 500 + 한국(코스피·코스닥)**을
스크리닝하는 FastAPI 웹앱. **✅ 배포 완료, 이후 기능 다수 추가하며 운영 중.**

- **라이브: https://minervini-screener-1gvr.onrender.com** (Render Free, push 시 자동 재배포)
  - ⚠️ Render Free 빌드+콜드스타트로 **재배포 반영까지 약 8분** 걸림(폴링으로 확인).
- GitHub: https://github.com/RealLight04/minervini-screener (public). **main 직접 커밋 → 자동 배포** 워크플로.
- ⚠️ `minervini-screener.onrender.com`(접미사 없는 주소)는 **타인의 인도 NSE 앱**이 선점 → 우리 건 `-1gvr` 접미사.

### 최신 추가 기능 (2026-06 기준)
- 한국 종목 지원(FinanceDataReader 수집 + OpenDART 재무), 캔들 차트(Chart.js/lightweight-charts), 관심종목.
- 분기 매출·영업이익·마진 추이, **3분기 연속 가속 판정**, **🏆 Code 33**(매출+영업이익 동시 가속).
- 시장 국면 신호등(breadth), 🔥 돌파 대기, **🧭 주도 섹터/테마**, 차트 **🎯 매수/손절 라인**.
- **미국 분기 EPS 이력 백필(Alpha Vantage)** → 종목상세에 **지표별 YoY/QoQ** 표시 + **🔥 EPS N분기 연속 성장(YoY)** 뱃지. (yfinance 5분기 한계 보완 — 아래 6번 참고)

---

## 1. 현재 상태 (✅ 끝난 것 / ⏳ 남은 것)

### ✅ 완료
- 전체 코드 구현 + **Render 배포 완료**, main push 시 자동 재배포로 운영 중.
- **DB 채워짐**: `screener.db` (~28MB) — **~809 종목**(미국 503 + 코스피·코스닥 ~300),
  일봉·재무 포함. **매일 자동 갱신**(아래 6번). 신호 분포 예: final_pass 21.
- 한국 종목 섹터 백필 완료(`scripts/collect_kr_sectors.py`): KOSPI 198/200, KOSDAQ 68/100.
  - yfinance가 섹터를 못 주는 종목에 남던 **거래소 소속부**(우량기업부 등)는 NULL로 정리 → 테마 오염 방지.
  - `fetch_kr_tickers`는 FDR 'Dept'를 sector로 안 씀 → 매일 재수집해도 테마 재오염 없음.
- **데이터 자동 갱신 가동 중**: GitHub Actions(`daily-refresh.yml`)가 평일 미장 마감 후 수집→커밋→
  Render 자동 재배포. 수동 트리거 실행으로 전 구간 검증 완료(2026-06-25).
- **DB 무한증가 방지**: `screener.prune_old_history`가 450일만 보존(스크리너 lookback 374일+여유),
  수집창도 450일로 정렬 → 매일 커밋해도 DB가 ~28MB에서 안정화.
- 최신 기능(주도 섹터/테마, Code 33, 차트 매수/손절 라인) 로컬·라이브 검증 완료. 코드 리뷰 통과.

### ⏳ 남은 작업 / 아이디어 (선택)
- **Render 자동배포 신뢰성**(권장): Render 대시보드에서 **Deploy Hook** URL 생성 →
  GitHub Secret으로 등록 → 워크플로 마지막에 `curl <hook>` 추가하면 자동배포가 큐에 막혀도
  확실히 배포됨. (오늘 연속 push 시 Render가 일시적으로 배포를 건너뛴 적 있음 → 수동배포로 해결)
- KOSDAQ 섹터 커버리지 개선(68/100): yfinance 외 보조 소스로 미분류 종목 채우기.
- git 히스토리 누적: screener.db를 매일 커밋하므로 blob이 쌓임(~28MB/commit, 평일). 수개월 후
  비대해지면 `git gc`/히스토리 정리 또는 LFS 고려.
- 포트폴리오 메모리(`project_portfolio.md`) 3단계 진행상황 갱신.

---

## 2. git 상태 정밀 스냅샷

### 2-A. 커밋 (main 브랜치)
- **origin/main과 동기화됨** (push 완료, 이후 모든 작업 main 직접 커밋 → 자동 배포).
- 최근 히스토리 확인: `git log --oneline -15`.
- 워크플로 파일은 히스토리에서 제거됨(아래 2-B의 토큰 스코프 문제). 복원은 4번 참고.

### 2-B. 리모트 / 인증
- origin: `https://github.com/RealLight04/minervini-screener.git` (이미 생성됨, **public, 빈 저장소**)
- gh 인증: 계정 `RealLight04` 로그인됨.
- ⚠️ **gh OAuth 토큰에 `workflow` 스코프 없음** → `.github/workflows/*` 포함 푸시는 거부됨.
  그래서 워크플로를 히스토리에서 제거한 상태. (해결책은 4번)

---

## 3. ⚠️ 반드시 알아야 할 함정 (실제로 겪은 것들)

1. **`pkill -f "uvicorn main:app"` 자기 자신 죽임**:
   명령 문자열에 `uvicorn main:app`이 들어있어 pkill이 자기 셸까지 매칭해 종료시킴(exit 144).
   → **브래킷 트릭** 사용: `pkill -f "[u]vicorn main:app"`
2. **백그라운드 서버는 서브셸 종료와 함께 죽음**: `(uvicorn ... &)` 방식 금지.
   → 별도 백그라운드 작업으로 띄울 것(`run_in_background`) 또는 `nohup`/`setsid`.
3. **WSL2 환경** (Ubuntu, Python **3.14**):
   - pip 설치 시 `pip install --break-system-packages` 필요(PEP 668). venv 생성 불가.
   - Windows 브라우저에서 `localhost` 접속 불안정 → `hostname -I` IP 사용, `--host 0.0.0.0` 바인딩.
   - **포트 8000은 다른 앱(tft-meta)이 점유** → 이 앱은 **8001** 사용.
   - Playwright 미지원 → 검증은 `curl` 또는 `cmd.exe /c start <url>`.
4. **screener.db는 .gitignore 안 함**(의도): 배포용 스냅샷이라 커밋에 포함. 21MB(100MB 한도 내).
5. **`/api/stats`·인덱스는 `_latest_screen_date()` 기준**: 스냅샷이 과거일이어도 0이 안 나오게.

---

## 4. 다음에 실행할 명령 (복붙용)

```bash
cd /home/pc100di/stock-screener

# (A) filter-branch 백업 ref 정리
git for-each-ref --format="%(refname)" refs/original/ | xargs -r -n1 git update-ref -d

# (B) push (워크플로 없는 현재 히스토리 그대로)
git push -u origin main

# (C) 배포 확인용 로컬 재기동
pkill -f "[u]vicorn main:app" 2>/dev/null; sleep 1
python3 -m uvicorn main:app --host 0.0.0.0 --port 8001 --log-level warning   # 백그라운드로
curl -s http://localhost:8001/api/stats   # {"date":"2026-06-20","total":503,...,"final_pass":17} 기대
```

### GitHub Actions 자동 갱신 — ✅ 이미 가동 중
워크플로(`daily-refresh.yml`)가 등록되어 평일 자동 실행 중. 상세·함정은 **6번** 참고.

---

## 5. Render 배포 (`render.yaml` 청사진)

- 무료 플랜은 디스크가 임시 → `ENABLE_SCHEDULER=false`(기본값), 커밋된 `screener.db` 스냅샷 서빙.
- 배포 방법: Render 대시보드에서 New → Blueprint → 이 GitHub 저장소 연결 (`render.yaml` 자동 인식).
  - 또는 브라우저로: `https://render.com/deploy?repo=https://github.com/RealLight04/minervini-screener`
  - WSL에서 열기: `cmd.exe /c start "" "<위 URL>"`
- 배포 후 검증:
  ```bash
  curl -s -o /dev/null -w "/ → %{http_code}\n" --max-time 90 https://<배포도메인>/
  curl -s --max-time 30 https://<배포도메인>/api/stats
  ```
- 참고(1단계 finance-tracker 경험): Render 무료는 첫 응답까지 콜드스타트로 수십 초 걸림.

---

## 6. 데이터 자동 갱신 (✅ 가동 중) — `.github/workflows/daily-refresh.yml`

**동작**: 평일 22:00 UTC(한국 07:00, 미장 마감 후) GitHub Actions가 자동 실행.
`daily_update.py`(미국) + `collect_kr.py`(한국)로 수집·재스크린 → `screener.db` 변경 시
커밋·push(`[skip ci]`) → **Render가 push 감지해 자동 재배포**. 수동 실행은 Actions 탭의
"Daily data refresh" → Run workflow, 또는 `gh workflow run daily-refresh.yml --ref main`.

- 약 10분 소요. yfinance가 GitHub 러너 IP에서도 정상 수집됨(검증 완료).
- 상태 확인: `gh run list --workflow=daily-refresh.yml`, `gh run view <id>`.

### 워크플로 단계 (2026-06-26 갱신)
1. `daily_update.py`(미국 수집·스크린) → 2. `collect_kr.py`(한국) →
3. **`backfill_eps.py --limit 25`**(미국 분기 EPS 백필, Alpha Vantage, `continue-on-error`) →
4. **`recompute_yoy.py`**(DB 누적분기로 YoY 재계산, 무인증) → 5. screener.db 커밋·push·Deploy Hook.
- 비밀키: yfinance·FDR·recompute는 무인증. **EPS 백필만 `ALPHAVANTAGE_API_KEY`(GitHub Secret) 필요**.
  - ⚠️ **AV 무료키 = 하루 25건 한도**. backfill_eps가 미완성 종목만 25건씩 점진 백필(skip 로직).
    매수후보 등 25종목은 완료(2026-06-26), **나머지 ~88종목은 ~4일에 걸쳐 자동 완성**.
  - 로컬 수동 백필: `python3 scripts/backfill_eps.py [--limit N] [TICKER ...]` → 후 `recompute_yoy.py`.
  - 데이터 정합성: daily_update(yfinance Diluted EPS)가 최근 5분기 덮어쓰고 AV(reportedEPS)는 과거분기
    담당 → 경계 분기 소소한 소스 혼합 가능(성장률 영향 미미, recompute_yoy가 매일 YoY 최신화).

### ⚠️ 워크플로 파일 수정 시 함정 (반드시 알 것)
- gh OAuth 토큰에 **`workflow` 스코프 없음**(`gist,read:org,repo`) → `.github/workflows/*`를
  **git push/`gh api`로 올리면 GitHub이 서버에서 거부**(`refusing to allow ... without workflow scope`).
- 그래서 최초 등록은 **GitHub 웹 UI**(Add file → Create new file에 경로
  `.github/workflows/daily-refresh.yml` 입력 후 붙여넣기)로 했음. **이후 수정도 같은 방식**이거나,
  사용자가 `gh auth refresh -s workflow -h github.com`(터미널·브라우저 1회)로 스코프 추가해야 push 가능.
  - ⚠️ `gh auth refresh`는 대화형 TTY 필요 → **에이전트가 대신 실행 불가**, 사용자가 직접.
- 단, **봇이 만드는 데이터 커밋(screener.db)은 워크플로 파일이 아니므로** 스코프 무관하게 push됨.

### ⚠️ Render 자동배포가 가끔 멈춤
- 짧은 시간에 연속 push하면 Render 무료가 배포를 건너뛰거나 큐에 멈춘 적 있음(이번에 겪음).
  → Render 대시보드 **Manual Deploy → Deploy latest commit**으로 해결. 항구 대책은 1번의 Deploy Hook.

---

## 7. 로컬에서 데이터 새로 수집하려면 (선택)

```bash
rm -f screener.db
python3 scripts/daily_update.py     # S&P 500 전체 수집+스크리닝, 약 15~20분 (yfinance)
```

---

## 8. 프로젝트 구조 핵심

| 경로 | 역할 |
|---|---|
| `main.py` | FastAPI 엔트리, lifespan에서 DB init + (옵션)스케줄러 |
| `app/screener.py` | **핵심 로직**: Trend Template 8조건, 펀더멘털, VCP 탐지, 매수/매도 신호 |
| `app/data_fetcher.py` | yfinance 주가·재무 수집, Wikipedia S&P500 목록, 한국(FinanceDataReader) |
| `app/routes.py` | `/`(매수·매도·🧭테마), `/stock/{ticker}`(Code33·플레이북), `/api/stats`, `/api/chart/{ticker}`, `/api/screen-now` |
| `app/models.py` | Stock / DailyPrice / Fundamental / ScreeningResult (market: US/KOSPI/KOSDAQ) |
| `templates/index.html`·`stock.html` | 매수목록·테마 / 종목상세·캔들차트(lightweight-charts) |
| `scripts/daily_update.py` | 수집+스크리닝 배치 (로컬/Actions에서 실행) |
| `scripts/backfill_eps.py` | 미국 분기 EPS 이력 Alpha Vantage 백필(하루 25건, 최근 12분기) |
| `scripts/recompute_yoy.py` | DB 누적분기로 매출·영익·EPS YoY 재계산(무인증, 네트워크 불필요) |
| `scripts/collect_kr_sectors.py` | 한국 종목 섹터 yfinance 백필 + 소속부 NULL 정리 |
| `scripts/collect_dart.py` | OpenDART로 한국 종목 재무 수집 (`docs_cache/`는 라이브러리 캐시, gitignore) |
| `screener.db` | 배포용 스냅샷 DB (커밋 포함, ~34MB) |
| `render.yaml` | Render 청사진 |
