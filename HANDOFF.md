# 🔁 작업 인계 런북 (Minervini Stock Screener)

> 이 파일은 **새 세션에서 그대로 이어서 같은 작업을 재현**하기 위한 런북입니다.
> 마지막 갱신: 2026-07-17. 원래 작업 디렉토리(`/home/pc100di/stock-screener`, WSL2)는
> **노트북 고장으로 소실됨** — 새 Windows 데스크톱에서 네이티브(비-WSL)로 재구축 완료.
> 현재 작업 디렉토리: `<사용자 폴더>\Desktop\minervini-screener` (Windows 네이티브, 9번 참고).

---

## 0. 한 줄 요약

마크 미너비니 SEPA(Trend Template + 실적 + VCP) 방법론으로 **S&P 500 + 한국(코스피·코스닥)**을
스크리닝하는 FastAPI 웹앱. **✅ 배포 완료, 이후 기능 다수 추가하며 운영 중.**

- **라이브(메인): https://minervini-screener-1gvr.onrender.com** (Render Free, push 시 자동 재배포)
  - ⚠️ Render Free 빌드+콜드스타트로 **재배포 반영까지 약 8분**, 유휴 후 첫 응답도 **최대 ~1분** 걸림.
- **라이브(백업, 콜드스타트 없음): https://minervini.tail6fe9f6.ts.net** — Tailscale Funnel로
  로컬 데스크톱을 직접 공개. PC가 켜져있는 한 상시 가동, 무료, 슬립 없음. **10번 참고.**
- GitHub: https://github.com/RealLight04/minervini-screener (public). **main 직접 커밋 → 자동 배포** 워크플로.
- ⚠️ `minervini-screener.onrender.com`(접미사 없는 주소)는 **타인의 인도 NSE 앱**이 선점 → 우리 건 `-1gvr` 접미사.

### 최신 추가 기능 (2026-06 기준)
- 한국 종목 지원(FinanceDataReader 수집 + OpenDART 재무), 캔들 차트(Chart.js/lightweight-charts).
- (2026-07) 관심종목 기능 제거, 증권사이트 스타일로 색상/타이포 개편.
- 분기 매출·영업이익·마진 추이, **3분기 연속 가속 판정**, **Code 33**(매출+영업이익 동시 가속).
- 시장 국면 신호등(breadth), 돌파 대기, **주도 섹터/테마**, 차트 **매수/손절 라인**.
- **미국 분기 EPS 이력 백필(Alpha Vantage)** → 종목상세에 **지표별 YoY/QoQ** 표시 + **EPS N분기 연속 성장(YoY)** 뱃지. (yfinance 5분기 한계 보완 — 아래 6번 참고)

### 2026-07-17 대규모 디자인 재설계 (⚠️ 아직 커밋/푸시 안 됨 — 아래 참고)
- 전체 이모지 제거, TradingView 실제 컬러(다크 `#131722`/틸그린 `#26a69a`/레드 `#ef5350`/블루 `#2962ff`) 기반 팔레트로 재설계. 카드 그림자 제거, 모서리 4px로 각지게, 탭을 TV식 밑줄 탭으로 변경.
- RS 순위·분기실적·수익률·거래량에 **강도 기반 히트맵 칩**(`app/routes.py`의 `heat_bg`/`rs_heat`/`ret_heat`/`growth_heat`/`vol_heat`, `color-mix` 활용 — 라이트/다크 자동 대응) 적용. `▲/▼` 방향 삼각형 추가(Yahoo Finance/Bloomberg 스타일).
- 종목상세 차트를 70/30 비대칭 레이아웃(`.chart-row`/`.chart-side`)으로 재구성 — RS 뱃지 + MA 정배열 상태바 + 핵심 수치를 차트 옆 사이드바에 배치. 캔들차트 위에 진입~손절 구간 음영 오버레이 추가.
- 홈 화면 시장국면 도넛 게이지 → TV식 밀도있는 숫자 스트립(`.stat-strip`)으로 교체, 시장국면+주도섹터를 나란히 배치(`.dash-row`).
- **⌘K 커맨드 팔레트** 추가 — `app/routes.py`의 `/api/search-tickers`(ILIKE 검색, 최대 20개) + `templates/base.html`의 모달/키보드 네비게이션. 기존 검색창 포커스 시 팔레트로 전환(무-JS 폴백은 기존 `/search` GET 그대로 유지).
- 차트 로딩 스켈레톤, 빈 상태(`empty_state` 매크로) 시각 개선, 부자연스러운 한국어 문구 일부 수정.
- **⚠️ 이 항목들은 로컬 워킹트리에만 있고 git commit/push가 안 된 상태** — `origin/main`(→ Render 자동배포) 은 예전 디자인 그대로. Tailscale Funnel(`minervini.tail6fe9f6.ts.net`)은 로컬 서버를 직접 공개하는 방식이라 최신 디자인 반영됨. 커밋/푸시 여부는 사용자 확인 후 진행 예정.

### 2026-07-17 자동(오토파일럿) 리뷰 기반 보안/품질 수정
- 아키텍트/보안/코드품질 3개 에이전트로 위 재설계분을 교차 검증 후 발견된 이슈 수정:
  - **`/api/screen-now`(수동 재스크리닝)가 인증 없이 공개 Funnel에 노출**되어 있던 걸 발견 — `config.py`의 `ADMIN_TRIGGER_TOKEN` 헤더(`x-admin-token`) 검증으로 잠금(미설정 시 404). **`.env`에 `ADMIN_TRIGGER_TOKEN=<임의의 긴 문자열>` 추가해야 이 엔드포인트를 다시 쓸 수 있음.**
  - `/alerts/subscribe`에 10분 재발송 쿨다운 추가(이메일 폭탄/Gmail 발송 쿼터 소진 방지).
  - `lightweight-charts` CDN 스크립트에 SRI(`integrity`/`crossorigin`) 해시 추가(공급망 변조 방지).
  - 커맨드 팔레트의 `innerHTML` 직접 조립을 `textContent` 기반 DOM 생성으로 교체(XSS 방지), 디바운스 타이머 정리 누락 수정.
  - 중복 코드 정리: 히트맵 칩 인라인 스타일 9곳 → `.heat-chip` 클래스, `empty_state`/`tv_link` 매크로를 `templates/_macros.html`로 통합(기존 index.html/vcp.html 각자 정의하던 것 제거).
  - 죽은 CSS 토큰 정리(`--font-display`, `--surface-2`, `--chart-*`, `.financial-num`).

### 2026-07-18 후속: 완성도 작업 + 포트 이슈
- 모바일 카드뷰에 히트맵 색상 테두리(border-left) 추가(5개 표), 티커 이니셜 아바타(실제 로고 API 대신 — 신규 외부 의존성/ToS 리스크 회피), VCP "형성→돌파" 서사 연결(양쪽 표에 스파크라인 추가 + "형성 N일 → 돌파" 문구).
- "덜어내기": 표 안의 TradingView 바로가기 아이콘 제거(종목상세 페이지에만 유지 — 표에서는 행마다 잦은 클러터였음), RS 배지의 "· 상위 10%" 중복 텍스트 제거.
- **버그 발견**: 매수 접근법의 "손익비"가 실제 계산 없이 `1 : 2.5`로 하드코딩되어 있던 걸 발견 — `tp.reward_pct / tp.risk_pct`로 실제 계산하도록 수정.
- **⚠️ 포트 8010에 좀비 프로세스(PID는 세션마다 다름) 발견** — 이전 세션에서 뜬 걸로 추정되는 python.exe가 "Services" 세션 소유로 떠 있어서 일반 `taskkill`로 못 죽임(관리자 권한 필요). 새 코드를 반영 못 한 채 계속 8010을 점유하고 있어서, **서버를 8011 포트로 새로 띄우고 Tailscale Funnel(`minervini.tail6fe9f6.ts.net`)을 8011로 재연결**했음. `desktop-p52dkp0.tail6fe9f6.ts.net`(보조 호스트명)은 아직 8010(좀비)을 가리키고 있어 미사용 시 무시해도 되지만, 필요하면 알려주면 됨. **다음 스크리너 실행 시 8010 포트가 계속 막혀있으면, 작업관리자(관리자 권한)에서 해당 python.exe를 직접 종료하거나 재부팅 필요.**

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

## 6. 데이터 자동 갱신 — GitHub Actions는 이제 **수동(비상용)만** (11번이 메인)

**2026-07-12 변경**: 로컬 PC가 상시 가동(10번 Tailscale Funnel)되면서, 자동 수집 주체를
**GitHub Actions → 로컬 Windows 작업 스케줄러**로 옮김(11번). 이유: GH Actions의 cron은
실측 지연이 20분~3시간+로 들쭉날쭉(같은 PC가 이미 24시간 켜져있으니 로컬에서 도는 게
지연도 없고, 로컬 DB를 바로 갱신하니 커밋·push·Render 재배포·30분 대기 없이 **즉시 반영**).

`daily-refresh.yml`은 **`schedule` 트리거를 제거하고 `workflow_dispatch`(수동 실행)만 남김** —
로컬 PC가 죽었을 때 Render/원격 스냅샷을 비상으로 최신화하는 용도로만 존재.
수동 실행: Actions 탭 "Daily data refresh" → Run workflow, 또는
`gh workflow run daily-refresh.yml --ref main`.

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
| `.claude/skills/run-minervini-screener/` | 에이전트용 실행 스킬(9번) — `SKILL.md` + `smoke.sh` |

---

## 9. 로컬 개발 — Windows 네이티브 (WSL 아님)

기존 WSL2(Ubuntu, Python 3.14) 개발 환경은 노트북 고장으로 소실됨. 새 데스크톱에서는
**WSL 없이 네이티브 Windows Python(3.11.8) venv**로 재구축 — 오히려 3번의 WSL2 함정들
(PEP668, localhost 접속 불안정, 포트 충돌)이 전혀 없어서 더 간단함.

### 실행 (에이전트/사람 공용, 원커맨드)

```bash
cd <이 저장소를 clone한 경로>   # 예: ~/Desktop/minervini-screener
bash .claude/skills/run-minervini-screener/smoke.sh
```

멱등적(idempotent) — venv 없으면 생성, 있으면 재사용, 이전 인스턴스는 자동 정리 후 재기동.
포트 기본 8010 (`PORT=8020 bash ...`로 변경 가능). 상세 내용·엔드포인트 표는
`.claude/skills/run-minervini-screener/SKILL.md` 참고. `.env` 없이도 기본값으로 바로 됨
(DART/AlphaVantage/Gmail 키는 선택 기능용, 서버 구동엔 불필요).

### ⚠️ Windows 네이티브 전용 함정 (WSL2와는 다름, 실제로 겪은 것)

1. **`pip install -r requirements.txt`가 `UnicodeDecodeError: 'cp949' codec ...`로 죽음**:
   `requirements.txt`에 한글 주석이 있는데, 한국어 로케일 Windows에서 pip의 인코딩
   자동감지가 시스템 코드페이지(cp949)로 폴백해서 발생. → **`export PYTHONUTF8=1`**
   설정 후 pip 실행하면 해결 (매번 재현되진 않음 — 로케일에 따라 다르니 항상 켜둘 것).
2. **Git Bash에 `pkill`이 아예 없음** (`pkill: command not found`, `2>/dev/null`에 가려서
   조용히 실패함 — 안 죽은 걸 죽은 줄 알고 넘어가기 쉬움). 또한 이 환경의 `ps`는 인자를
   안 보여줘서(`uvicorn main:app` 같은 커맨드라인 매칭 불가) venv 경로로 매칭해야 함:
   ```bash
   for winpid in $(ps aux | grep "[m]inervini-screener/venv" | awk '{print $4}'); do
     taskkill //F //PID "$winpid"
   done
   ```
   `ps aux`의 **4번째 컬럼(WINPID)**이 실제 `taskkill`에 필요한 Windows PID — 1번째 컬럼(MSYS PID)이나
   백그라운드 실행 직후의 `$!`는 래퍼 프로세스를 가리켜서 **틀린 PID**임(그걸 죽여도 서버는 안 죽음).
3. **Git Bash가 `curl -w`의 `/`로 시작하는 포맷 문자열을 깨뜨림**: MSYS가 경로 변환을 시도해서
   `C:/Program Files/Git/ -> 200` 같은 깨진 출력이 나올 수 있음. `->` 뒤 HTTP 코드는 정상이니
   무시해도 되지만, 아예 `/`로 시작 안 하게 쓰면 깔끔함.

---

## 10. Tailscale Funnel — 상시 공개 백업 URL (Render 콜드스타트 회피용)

Render 무료는 유휴 15분 후 슬립 → 재접속 시 콜드스타트로 최대 ~1분 걸림(실측: 52초).
**Tailscale Funnel**로 로컬 서버를 그대로 공개 인터넷에 노출하면, 이 PC가 켜져있는 한
**콜드스타트 없이 상시 접속 가능**, 완전 무료, 추가 가입도 불필요(이미 로그인된 계정 재사용).

- **라이브: https://minervini.tail6fe9f6.ts.net** (개인 Tailnet `tail6fe9f6.ts.net` 소유)
  - 참고: 예전 노트북은 개명 전 기기명 기준 주소를 썼음(현재 오프라인, 노트북 고장).
  - 이 데스크톱 기기명은 원래 자동생성된 이름이었는데 깔끔한 URL을 위해 `minervini`로 개명함.
- 로컬 서버(포트 8010)가 떠있는 상태에서:
  ```bash
  tailscale funnel --bg 8010          # 켜기 → https://<기기명>.tail6fe9f6.ts.net 로 공개
  tailscale funnel status             # 현재 활성 funnel 확인
  tailscale funnel --https=443 off    # 끄기
  ```
- 재부팅하면 로컬 uvicorn 프로세스는 꺼지므로, PC 재시작 시 9번의 `smoke.sh`로 재기동 필요
  (자동시작 등록은 아직 안 함 — 필요하면 작업 스케줄러/시작프로그램에 등록 고려, 8번 "남은 작업" 참고).

### ⚠️ 기기명(hostname) 변경 시 함정 (실제로 겪음)

- `tailscale set --hostname=<new>` 직후, **공개 DNS 전파에 몇 분의 갭**이 생김 — 이 사이엔
  옛 이름도(내부적으로 이미 라우팅이 끊겨 TLS 핸드셰이크 실패) 새 이름도(아직 전파 전) 둘 다
  깨져 보일 수 있음. `tailscale funnel status`엔 둘 다 "on"으로 나와도 실제로는 새 이름만 살아있음.
- **이 PC에서 하는 DNS 확인은 신뢰할 수 없음** — Windows가 Tailscale의 자체 DNS(NRPT)로
  `*.ts.net` 조회를 가로채서, 실제로 퍼졌는지와 무관하게 항상 성공한 것처럼 보임
  (`Resolve-DnsName`은 가로채짐, Git Bash `nslookup`은 대체로 우회함 — 도구마다 다름, 혼란의 원인).
  **진짜 확인 방법**: 외부 공개 리졸버로 직접 질의 + 실제 IP로 강제 접속.
  ```bash
  nslookup <name>.tail6fe9f6.ts.net 8.8.8.8      # 공개 전파 여부 확인
  curl --resolve <name>.tail6fe9f6.ts.net:443:<위에서_나온_IP> https://<name>.tail6fe9f6.ts.net/api/stats
  ```
- **미해결/관찰 중인 이슈**: PC 브라우저에선 되는데 **휴대폰 데이터망에서 안 되는** 경우 있었음
  (Tailscale 앱 미설치, 데이터망 사용 중에도 발생) — 통신사 자체 DNS 리졸버가 구글/클라우드플레어보다
  전파가 느린 것으로 추정. 비행기모드 토글 또는 10~20분 대기로 보통 해결. 계속 안 되면 기기명을
  원래대로 되돌리는 것도 고려(단, 되돌릴 때도 같은 전파 갭이 반복됨).

---

## 11. 로컬 데이터 자동 갱신 — Windows 작업 스케줄러 (2026-07-12 추가, ✅ 등록 완료)

**목표**: 각 시장 마감 10분 후 지연 없이 로컬 `screener.db`를 갱신(6번 참고 — GH Actions는
이제 비상용). 새로 만든 진입점 `scripts/local_refresh.py`가 시장별 수집 스크립트를 호출.

### 등록된 작업 3개 (모두 비관리자 권한으로 생성됨 — 현재 로그인 계정 소유)

| 작업 이름 | 트리거 | 요일(KST 기준) | 실행 |
|---|---|---|---|
| `MinerviniRefreshKR` | 매일 15:40 | 월~금 | `collect_kr.py` (코스피/코스닥 마감 15:30 + 10분) |
| `MinerviniRefreshUS-EDT` | 매일 05:10 | **화~토** | `daily_update.py` + `backfill_eps.py` + `recompute_yoy.py` |
| `MinerviniRefreshUS-EST` | 매일 06:10 | **화~토** | 위와 동일 |

- `Get-ScheduledTask -TaskName "MinerviniRefresh*"`로 확인 가능. 각각 바로가기 배치파일
  (`C:\Users\<사용자>\Desktop\minervini-refresh-{kr,us-edt,us-est}.bat`, 저장소 밖·git 비대상)이
  `venv\Scripts\python.exe scripts\local_refresh.py --mode ... [--season ...]`를 호출.
- `--season edt`/`--season est`는 **자기 자신이 스킵 여부를 판단**한다 — 실행 시점의 뉴욕 UTC
  오프셋을 확인해서 해당 시즌이 아니면 조용히 종료(0). 그래서 두 작업을 매일 걸어놔도 실제로는
  하루에 하나만 진짜로 돈다. `.github/workflows/daily-refresh.yml`의 예전 "Decide run mode"
  스텝과 완전히 같은 방식(로직을 그대로 로컬로 옮김).

### ⚠️ 나스닥 마감 시각 → KST 변환 시 서머타임 방향에 주의 (실제로 헷갈렸던 부분)

나스닥은 항상 현지시각 16:00 마감이지만, 한국은 DST가 없어서 **미국이 서머타임(EDT)일 때
오히려 한국시각으로는 더 이르다**(05:00), 표준시(EST)일 때 더 늦다(06:00) — 직관과 반대 방향이라
착각하기 쉬움. 그래서 `MinerviniRefreshUS-EDT`/`-EST` 요일도 **화~토**(월~금이 아님)로 설정함:
미국 월요일 마감분이 한국시각으로는 화요일 새벽에 들어오기 때문. `MinerviniRefreshKR`만 월~금
(코스피/코스닥은 당일 오후 마감이라 요일이 안 밀림).

### ⚠️ 이 3개는 비관리자 권한으로 만들어져서 "로그인 중일 때만" 실행됨

`/RU SYSTEM`이나 `/RU <계정> /RP <비번>`(로그아웃 상태에서도 실행)은 관리자 권한이 필요해서
에이전트가 비대화형으로는 만들 수 없었음(`ERROR: Access is denied.`) — 반면 평범한
WEEKLY/DAILY 트리거는 현재 로그인 계정 소유로 비관리자 권한에서도 그냥 만들어짐. 이 PC는
평소 로그인 상태로 상시 켜두는 게 전제라 실사용엔 문제 없지만, **재부팅 후 아무도 로그인 안 하면
이 3개도, 아래 웹서버 자동시작도 실행되지 않는다.**

### ✅ 웹서버 재부팅 자동시작 — 로그인 자동시작으로 해결(관리자 불필요)

**해결됨(로그인 트리거).** 관리자 권한이 필요한 `ONSTART` Task Scheduler 대신, **시작프로그램
폴더**로 우회했다 — `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\minervini-server.vbs`가
로그인 시 `scripts/start_server.ps1`을 숨김 실행해 8011에 uvicorn을 띄운다(이미 떠 있으면
중복 실행 안 함). Tailscale은 자동 시작 서비스라 퍼널(minervini... → 127.0.0.1:8011)이
부팅 시 알아서 복구되므로, **재부팅 후 로그인하면 공개 URL이 자동으로 되살아난다.**
- 런처 스크립트 `scripts/start_server.ps1`은 리포에 커밋됨. `.vbs`는 사용자 계정 전용이라
  저장소 밖(위 Startup 경로)에 있음.

**한계 / 더 강하게 하려면(선택, 관리자 필요):**
- 로그인 트리거라 **로그인 전(잠금화면)엔 안 뜬다.** 아무도 로그인 안 한 채 재부팅되면 다음
  로그인까지 다운. 로그인 없이 부팅 즉시 띄우려면 관리자 권한으로 ONSTART 작업 또는 서비스
  등록이 필요하다:
  ```
  schtasks /create /tn "MinerviniScreenerStartup" /tr "powershell -NoProfile -WindowStyle Hidden -File C:\Users\Liam\Desktop\minervini-screener\scripts\start_server.ps1" /sc onstart /delay 0001:00 /ru "$env:USERNAME" /rp * /rl highest /f
  ```
- 시작프로그램은 로그인 시 1회 실행이라, 서버가 나중에 크래시하면 자동 재시작은 안 된다.
  크래시 자동복구까지 원하면 NSSM 등으로 서비스화(관리자 필요).

### ⚠️ 로컬 venv에 `finance-datareader` 없어서 KR 수집이 조용히 실패했던 실화

`smoke.sh`(웹서버 구동용)는 `requirements.txt`만 설치해서 `finance-datareader`가 없다 —
`local_refresh.py --mode kr`을 처음 실행했을 때 `FinanceDataReader 미설치`로 즉시 실패했음.
`venv\Scripts\python.exe -m pip install finance-datareader`로 1회 설치해서 해결(SKILL.md
Gotchas에도 기록). 이 venv를 다시 만들 일이 있으면(새 PC 등) 이 스텝을 빼먹지 말 것.

---

## ⏳ 보류 항목: VCP 품질점수 v2 (랭킹 개선) — 몇 달 뒤 표본외 검증 후 적용 결정

**상태: 미적용(라이브 점수 안 건드림).** 5.5년(2021~2026) 시점복원 백테스트로 "강세장에서
거래량을 어떻게 판단할지"를 세분화한 결과, 두 신호가 유효했다:
- **베이스 dry-up이 가장 신뢰할 단조 신호** — 마를수록 성과↑ (dryup≤0.6 → +20일 +3.3%/초과
  +2.96%p, dryup>1.1 늘어남 → 초과 −0.30%p).
- **과열 매집(50일 U/D≥1.5)은 되돌림 위험↑** (초과 +0.05%p, 손절 33%).
- 반면 돌파 '당일' 거래량은 품질 신호가 아니었음(1.4~2.0x가 최악, 조용한 돌파가 최고) →
  이 부분은 `volume_verdict` 문구를 이미 수정해 반영(v1, 적용됨).

**제안 코드(`app/screener.py` `_vcp_quality`):** dry-up을 이분법(+15)→등급제로,
과열 매집 감점 추가.
```python
    # 현재:  + (15 if r.vcp_volume_dryup else 0)
    # v2 제안:
    dry = r.dryup_ratio
    if dry is not None:
        dryup_pts = 18 if dry <= 0.6 else 12 if dry <= 0.85 else 4 if dry <= 1.1 else 0
    else:
        dryup_pts = 15 if r.vcp_volume_dryup else 0
    overheat = -8 if (r.ud_volume_ratio is not None and r.ud_volume_ratio >= 1.5) else 0
    # q 식에서 (15 if vcp_volume_dryup ...) 를 dryup_pts 로 교체하고 끝에 + overheat 추가
```
점수 최대치가 ~95→~98로 거의 같아 `VCP_QUALITY_ALERT=70`은 그대로 유효. 적용 시 재스크리닝 1회 필요.

**왜 지금 안 하나 / 검증 방법:** 이 가중치는 하나의 유니버스·과거 표본에 맞춘 휴리스틱이라
과적합 위험이 있다. 같은 과거 데이터로 재백테스트하면 표본이 같아 의미 없음. 대신 레지스트리
(`vcp_events`)가 지금부터 실제 forming→broke_out/failed 결과를 자동 누적하므로, **몇 달 뒤
(대략 2026-10 이후) 그 실제 결과에 v2 점수식을 대입해 "고점수 셋업이 실제로 더 잘 됐는지"를
표본외로 확인**한 뒤 적용 여부를 결정한다.

**관련 스크립트(리포에 영구 보존):** `scripts/research/` — `pull_history.py`(장기 가격 수집),
`vcp_volume_backtest.py`(구간 분석), `README.md`(실행법·결론·후속 검증 계획). 생성 데이터
(`scripts/research/data/`)는 gitignore이므로 `pull_history.py`로 재생성해서 쓴다.
(원 분석은 세션 스크래치에서 진행했고, 재현용 스크립트를 이 폴더로 옮겨 보존했다.)
