# research/ — 일회성 백테스트·검증 스크립트

프로덕션 파이프라인과 분리된 연구용 스크립트. 결과 데이터(`data/`)는 재생성 가능하므로
git에 넣지 않는다(`.gitignore`). 스크립트만 보존한다.

## 파일

| 파일 | 용도 |
|---|---|
| `pull_history.py` | screener.db의 활성 종목으로 장기 가격 히스토리를 `data/prices.sqlite`로 수집 (US=yfinance, KR=FinanceDataReader). 시작일 인자 지정 가능. |
| `vcp_volume_backtest.py` | 강세장에서 거래량을 어떻게 볼지 세분화 검증(in-sample). 돌파 당일 거래량 vs 베이스 U/D 매집 vs dry-up. |

## 실행

```bash
python scripts/research/pull_history.py 2021-01-01   # 데이터 수집(수 분)
python scripts/research/vcp_volume_backtest.py        # 백테스트(수 분)
```
`finance-datareader` 필요(KR): `venv/Scripts/python.exe -m pip install finance-datareader`

## 핵심 결론 (2021~2026, BULL 국면)

- **베이스 dry-up이 가장 신뢰할 단조 신호** — 마를수록 성과↑.
- **돌파 '당일' 거래량은 품질 신호 아님** — 1.4~2.0x가 최악, 조용한 돌파가 최고.
  → `app/routes.py`의 `volume_verdict` 돌파 문구는 이 결과를 이미 반영(적용됨).
- **과열 매집(U/D≥1.5)은 되돌림 위험↑.**

## ⏳ 보류 중인 후속 작업 (HANDOFF.md 참고)

`_vcp_quality` v2(dry-up 등급제 + 과열 감점)는 **과적합 우려로 미적용.** 같은 과거 데이터
재백테스트는 무의미(표본 동일)하므로, **레지스트리(`vcp_events`)에 쌓인 실제 forming→
돌파/실패 결과로 표본외(out-of-sample) 검증**한 뒤 적용을 결정한다(대략 2026-10 이후).
그 시점의 검증 스크립트는 이 폴더의 로직을 재사용하되, 신호를 `detect_vcp` 재생이 아니라
`vcp_events`에서 직접 읽어 v2 점수와 실제 결과의 상관을 보면 된다.
