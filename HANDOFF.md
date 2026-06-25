# 🔁 작업 인계 런북 (Minervini Stock Screener)

> 이 파일은 **새 세션에서 그대로 이어서 같은 작업을 재현**하기 위한 런북입니다.
> 마지막 갱신: 2026-06-25. 작업 디렉토리: `/home/pc100di/stock-screener`

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

---

## 1. 현재 상태 (✅ 끝난 것 / ⏳ 남은 것)

### ✅ 완료
- 전체 코드 구현 + **Render 배포 완료**, main push 시 자동 재배포로 운영 중.
- **DB 채워짐**: `screener.db` (~34MB) — **803 종목**(미국 503 + 코스피 200 + 코스닥 100),
  일봉·재무 포함. 최신 스크리닝일 **2026-06-22**, 신호 분포 total 803 / technical_pass 146 / final_pass 28.
- 한국 종목 섹터 백필 완료(`scripts/collect_kr_sectors.py`): KOSPI 198/200, KOSDAQ 68/100.
  - yfinance가 섹터를 못 주는 종목에 남던 **거래소 소속부**(우량기업부 등)는 NULL로 정리 → 테마 오염 방지.
- 최신 기능(주도 섹터/테마, Code 33, 차트 매수/손절 라인) 로컬·라이브 검증 완료. 코드 리뷰 통과.

### ⏳ 남은 작업 / 아이디어 (선택)
- KOSDAQ 섹터 커버리지 개선(68/100): yfinance 외 보조 소스로 미분류 32종목 채우기.
- (선택) GitHub Actions 데이터 자동 갱신 워크플로 복원(아래 6번). 현재는 수동 `daily_update.py`.
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

### GitHub Actions 자동 갱신을 살리고 싶다면 (선택)
워크플로 파일은 푸시 차단 때문에 제거했음. 다시 넣으려면:
```bash
gh auth refresh -s workflow      # 브라우저 1회 인증 (workflow 스코프 추가)
mkdir -p .github/workflows
# 아래 6번의 워크플로 내용을 refresh-data.yml 로 저장
git add -f .github/workflows/refresh-data.yml && git commit -m "ci: 데이터 자동갱신 워크플로" && git push
```

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

## 6. 보존: 삭제된 GitHub Actions 워크플로 내용

> 히스토리에서 제거되며 디스크에서도 사라졌으므로 여기 보존. 4번 방법으로 복원 시 사용.

```yaml
name: Refresh screener data

# 기본은 수동 실행. 매일 자동 갱신하려면 아래 schedule 주석을 해제하세요.
on:
  workflow_dispatch:
  # schedule:
  #   - cron: "0 22 * * 1-5"   # 평일 22:00 UTC (미국 장 마감 후)

permissions:
  contents: write

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Collect data + screen
        run: python scripts/daily_update.py
      - name: Commit updated snapshot DB
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add screener.db
          git diff --staged --quiet || git commit -m "chore: refresh screener.db snapshot [skip ci]"
          git push
```

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
| `scripts/collect_kr_sectors.py` | 한국 종목 섹터 yfinance 백필 + 소속부 NULL 정리 |
| `scripts/collect_dart.py` | OpenDART로 한국 종목 재무 수집 (`docs_cache/`는 라이브러리 캐시, gitignore) |
| `screener.db` | 배포용 스냅샷 DB (커밋 포함, ~34MB) |
| `render.yaml` | Render 청사진 |
