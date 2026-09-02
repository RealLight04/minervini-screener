# CLAUDE.md — 웹사이트 개발 원칙

Mark Minervini SEPA/VCP 주식 스크리너. **FastAPI + Jinja2 서버렌더링 + SQLite**,
로컬(Windows) 구동 + Tailscale Funnel로 공개(`https://minervini.tail6fe9f6.ts.net`).
이 문서는 이 사이트를 고칠 때 반드시 지킬 원칙을 정리한다. 자세한 운영 절차는 `HANDOFF.md`,
실행/스모크는 `.claude/skills/run-minervini-screener/` 참고.

## 아키텍처 원칙

- **서버렌더링 고수.** React/Tailwind/빌드툴 없음. 화면은 Jinja2 템플릿 + **순수 CSS**로만.
  새 기능도 이 방식으로 — SPA/프론트 프레임워크 도입 금지(스택 단순성이 이 프로젝트의 전제).
- **테마는 CSS 커스텀 프로퍼티로.** 라이트/다크는 `:root` 변수(`--green`/`--red`/`--surface`
  등)와 `:root[data-theme=...]`로 처리. 색을 하드코딩하지 말고 항상 토큰(변수)을 쓴다.
  차트(JS)도 `getComputedStyle`로 같은 토큰을 읽어 UI와 어긋나지 않게.
- **DB는 커밋되는 스냅샷.** `screener.db`(SQLite)는 배포 스냅샷이라 git에 **커밋**한다
  (`.gitignore` 하지 않음). Postgres 경로는 백업용.

## ⚠️ 가장 자주 실수하는 것 — 스냅샷 재계산

- `screening_results`·`signal_reason`·`vcp_events`는 **일일 배치 `run_daily_screen()`이
  만든 스냅샷**이다. `app/screener.py`의 로직(신호 문구·VCP 판정·품질 등)을 고쳐도
  **DB의 기존 행은 그대로**다 → 반드시 **재스크리닝**해야 화면에 반영된다:
  ```
  PYTHONUTF8=1 ENABLE_SCHEDULER=false venv/Scripts/python.exe -c "from app.database import SessionLocal; from app.screener import run_daily_screen; db=SessionLocal(); run_daily_screen(db); db.close()"
  ```
- 재스크리닝 후 **서버 코드도 재시작**해야 새 파이썬 로직이 로드된다(서버는 요청 시 DB만
  읽으므로 데이터 변경은 즉시 보이지만, 함수 변경은 재시작 필요).

## 스키마 변경 원칙

- 컬럼 추가 = **① `app/models.py`에 Column 추가 + ② `app/database.py`의 `migrations`
  dict에 추가**. SQLite는 `init_db()`가 앱 시작 시 자가치유 ALTER를 한다. 이 두 곳을
  같이 안 고치면 기존 DB에서 `no such column`으로 깨진다.
- 예외: `app/database.py` 밖에서 직접 ALTER를 하는 스크립트가 셋 있다 —
  `scripts/backfill_eps.py`(fundamentals), `scripts/collect_fundamentals.py`(stocks),
  `scripts/collect_q_fundamentals.py`(fundamentals). 각자 자체 컬럼 dict를 들고 있어
  해당 테이블의 컬럼을 바꿀 땐 거기도 함께 본다.
- 나중에 검증할 값은 정리(prune)되기 전에 영구 테이블(`vcp_events`)에 **스냅샷**으로 남겨라
  (`ScreeningResult`는 90일 후 삭제됨).

## 디자인·UX 원칙 (일관성 = AI티 안 나는 UI의 핵심)

- **축마다 값 하나.** radius 성격 하나(샤프 3~4px), 컨트롤 높이 셋 하나, 아이콘 패밀리 하나
  (currentColor 스트로크 SVG). 섞지 말 것.
- **색 = 심각도, 장식 아님.** 보통/정상은 중립 회색, 색은 주의가 필요한 소수에만. 같은 값엔
  같은 색. 정반대 의미(기회 vs 위험)에 사실상 같은 색을 쓰지 말 것.
- **이모지를 UI 아이콘으로 쓰지 말 것**(🔍🌙 등 → OS마다 멀티컬러라 테마·톤 깨짐). 라인 SVG로.
- **문구는 간결한 개조식으로.** 신호·조언 문구는 `~습니다`/`~하세요` 종결어미를 빼고 명사형으로
  짧게 끝맺는다(예: "50일선 무너지며 추세 약해짐. 보유 중이면 매도·비중 축소 고려"). 단, 대시·등호로
  절만 이어붙인 태그 나열은 여전히 금지 — 읽히는 짧은 구로. 목적성·간결성·명확성, 한국어 UI 기준.
  데이터가 반박하면(예: "거래량 없는 돌파=의심"은 백테스트상 틀림) 통념보다 데이터에 맞춘다.
- **접근성**: `focus-visible`, 라벨-입력 연결, 대비 WCAG AA, 터치 타깃 ≥44px,
  `prefers-reduced-motion` 존중, 이미지 `alt`, 12px 미만 텍스트 금지.
- 참고 규칙셋: `.claude/`의 styleseed 문서(DESIGN-LANGUAGE / VISUAL-CRAFT / UX-WRITING),
  감사는 `/ss-review`.

## 런타임·배포 원칙

- 서버: `uvicorn main:app --port 8011`. Tailscale Funnel(자동시작 서비스)이 8011을
  `minervini.tail6fe9f6.ts.net`로 노출. 서버 프로세스는 `ENABLE_SCHEDULER=false`
  (데이터 갱신은 별도 예약작업 담당).
- **로그인 자동시작**: 시작프로그램 폴더의 `minervini-server.vbs` → `scripts/start_server.ps1`
  (8011, 중복 실행 방지). 재부팅+로그인 시 URL 자동 복구.
- **서버 파이썬 코드를 바꿨으면 `scripts/start_server.ps1 -Restart`.** 인자 없이 호출하면
  8011이 이미 떠 있을 때 아무것도 하지 않고 종료하므로 새 코드가 반영되지 않는다.
- 데이터 갱신: 로컬 예약작업 3개(`MinerviniRefresh*`)가 바탕화면 `minervini-refresh-*.bat`를
  절대경로로 호출 → 그 파일들·`minervini-screener` 폴더 경로를 옮기면 깨진다.

## 병렬 세션 규칙 (여러 세션을 동시에 쓸 때)

모든 쓰기가 단일 자원 셋(`screener.db`, 포트 8011, git 인덱스)에 몰린다. 그래서 세션은
기능별로 나누지 말고 **쓰기 권한**으로 나눈다. 쓰기 1명, 읽기 N명.

- **Owner** (본 체크아웃 `minervini-screener`): 모든 소스 편집, 재스크리닝, 8011 재시작,
  모든 커밋. `templates/base.html`·`app/routes.py`·`app/screener.py`·`screener.db`·
  `HANDOFF.md`는 Owner 전용(최근 커밋 대부분이 이 파일들에 몰린다).
- **Builder** (워크트리 `../minervini-build`, 브랜치 `build`): 자기 체크아웃 안에서만 편집,
  미리보기는 8030. `screener.db`는 절대 스테이징하지 않는다(바이너리는 병합 불가).
- **Reader** (아무 세션): 읽기·분석·조사·리뷰·패치 초안만. 결과는 텍스트로 Owner에 전달.

Owner 외 전원 금지: git `add`/`commit`/`stash`/`checkout`/`restore`/`reset`/`clean` ·
8010·8011 포트에 서버 기동(**8010은 공개 퍼널에 연결돼 있다**) · `bash smoke.sh`(기본 포트가
8010이고 프로세스 종료를 포트 대신 실행경로로 매칭) · 재스크리닝·수집 스크립트
(`local_refresh.py`, `daily_update.py`, `collect_kr.py`, `send_alerts.py`, `backfill_eps.py`) ·
`GET /api/screen-now`(공개 웹 프로세스 안에서 배타적 DB 쓰기를 유발) · `pip install`(venv 공용).

주의:
- 본 체크아웃의 템플릿 저장은 다음 요청부터 **공개 URL에 즉시 반영**된다(Jinja auto_reload).
  반쯤 저장된 `base.html`은 실제 방문자에게 500. UI 실험은 워크트리에서 한다.
- 재스크리닝은 하루 한 번만, 예약 갱신(한국장 15:40 / 미국장 05:10)에서 15분 이상 떨어뜨려서.
  같은 날 중복 실행은 `vcp_events`를 오염시킨다(유니크 제약 없음).
- `DATABASE_URL`은 CWD 상대경로다. 다른 디렉터리에서 스크립트를 돌리면 빈 DB가 새로 생긴다.
- 커밋은 항상 경로 명시. `git add -A`/`commit -a` 금지(미추적 파일과 51MB DB를 쓸어담는다).

워크트리 미리보기 서버 — 공개 사이트와 라이브 DB를 건드리지 않는다.
데이터는 라이브 DB의 사본을 쓴다. 사본이 낡으면 화면이 프로덕션과 달라지니 필요할 때 갱신한다.
```bash
# 1) 미리보기 DB를 지금 데이터로 새로 뜨기 (본 체크아웃에서 실행)
venv/Scripts/python.exe -c "import sqlite3;s=sqlite3.connect('screener.db');d=sqlite3.connect(r'C:/Users/Liam/Desktop/minervini-build/data/preview.db');s.backup(d);d.close();s.close()"

# 2) 워크트리에서 8030으로 띄우기 (퍼널이 8010·8011만 노출하므로 8030은 외부에서 안 보인다)
cd /c/Users/Liam/Desktop/minervini-build
DATABASE_URL=sqlite:///C:/Users/Liam/Desktop/minervini-build/data/preview.db \
ENABLE_SCHEDULER=false \
/c/Users/Liam/Desktop/minervini-screener/venv/Scripts/python.exe \
  -m uvicorn main:app --host 127.0.0.1 --port 8030
```

## 튜닝·검증 원칙

- 매직넘버는 `config.py` 상수로(예: `VCP_*`). 값은 초기 휴리스틱임을 주석으로 명시.
- **휴리스틱은 과적합을 경계.** 같은 과거 데이터 재백테스트는 검증이 아니다. 새 가중치는
  레지스트리(`vcp_events`)에 쌓이는 **실제 결과로 표본외(out-of-sample) 검증** 후 적용.
- 연구·백테스트 스크립트는 `scripts/research/`에(리포 보존). 생성 데이터 중 gitignore된 것은
  `scripts/research/data/`와 `data/backups/`뿐이다(루트 `data/`는 무시 대상이 아니니 새 경로를
  쓸 땐 먼저 `git check-ignore`로 확인).

## Git 원칙

- 로그(`*.log`)·연구 생성물·`alerts.db`·`.env`는 커밋 금지(gitignore). 비밀값은 `.env`만.
- 데이터/스키마가 바뀌면 `screener.db` 스냅샷을 함께 커밋. 커밋 메시지는 한국어 OK.
- 커밋·푸시는 사용자가 요청할 때만.
