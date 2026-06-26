"""
미국 종목의 분기 EPS '이력'을 Alpha Vantage EARNINGS에서 백필 → Fundamental(Q) 갱신.

배경: yfinance 무료는 분기 EPS를 ~5분기만 줘서 YoY를 1분기치밖에 못 만든다.
      Alpha Vantage EARNINGS는 reportedEPS를 20분기+ 줘서, 백필하면
      recompute_yoy.py가 '진짜 3분기 연속 YoY 가속'(미너비니식)을 채울 수 있다.

대상: 미국(market='US') 종목 중 최신 스크리닝에서 final_pass 또는 technical_pass인
      '관심권' 종목(매수후보가 정작 EPS 추세가 궁금한 대상). 전체 504개는 무료 한도 밖.

무료 한도: Alpha Vantage 무료키는 분당 약 5건·하루 약 25건. 그래서
  - 호출 간 15초 sleep(분당 4건, 여유)
  - 이미 EPS가 6분기 이상 채워진 종목은 건너뜀(재실행 시 이어받기)
  - 일일 한도 메시지를 만나면 깔끔히 중단(다음날 이어 실행)

실행: python3 scripts/backfill_eps.py [--limit N] [TICKER ...]
  --limit N : 이번 실행에서 최대 N개 종목만 처리(기본 25 = 일일 한도)
  TICKER…   : 특정 종목만 (예: MNST AAPL)
"""
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_eps")

from app.database import SessionLocal
from app.models import Fundamental, ScreeningResult, Stock
from config import settings

AV_URL = "https://www.alphavantage.co/query"
SLEEP_SEC = 15          # 분당 4건(무료 5건/분 한도 안)
DEFAULT_LIMIT = 25      # 무료 일일 한도
FULLY_BACKFILLED_QUARTERS = 9   # EPS 분기가 이만큼 차 있으면 백필 완료로 간주
STALE_QUARTER_DAYS = 100    # 최신 EPS 분기가 이보다 오래되면 새 분기 보고 가능성 → 재수집
KEEP_QUARTERS = 12      # 종목당 백필할 최근 분기 수(3분기 YoY엔 ~9면 충분, DB 비대화 방지)
DATE_TOL_DAYS = 10      # 회계분기말 ↔ 기존 분기 row 날짜 매칭 허용오차


def _target_tickers(db, only):
    """미국 관심권(final_pass or technical_pass) 종목 티커 목록."""
    if only:
        return list(only)
    latest = db.query(ScreeningResult.screen_date).order_by(ScreeningResult.screen_date.desc()).first()
    if not latest:
        return []
    screen_date = latest[0]
    rows = (
        db.query(Stock.ticker)
        .join(ScreeningResult, ScreeningResult.stock_id == Stock.id)
        .filter(
            ScreeningResult.screen_date == screen_date,
            Stock.market == "US",
            (ScreeningResult.final_pass == True) | (ScreeningResult.technical_pass == True),  # noqa: E712
        )
        .order_by(ScreeningResult.final_pass.desc(), ScreeningResult.rs_rank.desc())
        .all()
    )
    return [t for (t,) in rows]


def _needs_backfill(db, stock_id):
    """백필이 필요한가? (1) EPS 분기가 충분히 안 쌓였거나, (2) 최신 EPS 분기가
    오래됐으면(새 분기가 보고됐을 가능성) 다시 받는다. 둘 다 아니면 건너뜀.
    → 일일 파이프라인에서 미완성 종목을 점진 완성하고, 분기 실적 시즌엔 최신화한다."""
    from sqlalchemy import func
    rows = (
        db.query(func.count(Fundamental.id), func.max(Fundamental.period_date))
        .filter(
            Fundamental.stock_id == stock_id,
            Fundamental.period_type == "Q",
            Fundamental.eps.isnot(None),
        )
        .first()
    )
    n, latest = (rows[0] or 0), rows[1]
    if n < FULLY_BACKFILLED_QUARTERS:
        return True
    if latest is None:
        return True
    return (date.today() - latest).days > STALE_QUARTER_DAYS


def _fetch_av_earnings(ticker):
    """Alpha Vantage EARNINGS 호출 → quarterlyEarnings 리스트. 한도/오류 시 (None, 사유)."""
    try:
        resp = requests.get(
            AV_URL,
            params={"function": "EARNINGS", "symbol": ticker, "apikey": settings.ALPHAVANTAGE_API_KEY},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return None, f"요청 실패: {e}"

    # 무료 한도 초과 시 Alpha Vantage는 Note/Information 키로 안내(데이터 없음)
    if "Note" in data or "Information" in data:
        return None, "LIMIT:" + (data.get("Note") or data.get("Information") or "")[:120]
    qe = data.get("quarterlyEarnings")
    if not qe:
        return None, "데이터 없음(상장 이력 짧거나 미지원 티커)"
    return qe, None


def _to_float(v):
    if v in (None, "", "None", "none"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _match_or_new(db, stock_id, qdate):
    """qdate(회계분기말)에 해당하는 기존 Q row 반환, 없으면 새로 만들어 add."""
    lo = date.fromordinal(qdate.toordinal() - DATE_TOL_DAYS)
    hi = date.fromordinal(qdate.toordinal() + DATE_TOL_DAYS)
    row = (
        db.query(Fundamental)
        .filter(
            Fundamental.stock_id == stock_id,
            Fundamental.period_type == "Q",
            Fundamental.period_date >= lo,
            Fundamental.period_date <= hi,
        )
        .order_by(Fundamental.period_date.desc())
        .first()
    )
    if row:
        return row, False
    row = Fundamental(stock_id=stock_id, period_type="Q", period_date=qdate)
    db.add(row)
    return row, True


def backfill_one(db, ticker):
    """한 종목의 EPS 이력 백필. 반환: (성공여부, 메시지)."""
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if not stock:
        return False, "DB에 없는 종목"

    qe, err = _fetch_av_earnings(ticker)
    if qe is None:
        return False, err

    # AV는 상장 이래 전체(수십 분기)를 newest-first로 준다. 3분기 YoY엔 최근 KEEP_QUARTERS면
    # 충분하고, 매일 커밋되는 DB 비대화를 막으려 최근 분기만 취한다.
    qe = qe[:KEEP_QUARTERS]

    added = updated = 0
    for item in qe:
        fde = item.get("fiscalDateEnding")
        eps = _to_float(item.get("reportedEPS"))
        if not fde or eps is None:
            continue
        try:
            qdate = datetime.strptime(fde, "%Y-%m-%d").date()
        except ValueError:
            continue
        row, is_new = _match_or_new(db, stock.id, qdate)
        # Alpha Vantage reportedEPS로 EPS 계열을 일관되게 채움(YoY 정합성).
        # 매출·영업이익은 건드리지 않음(yfinance/DART 소관).
        row.eps = eps
        if is_new:
            added += 1
        else:
            updated += 1
    db.commit()
    return True, f"분기 추가 {added}, 갱신 {updated}"


def main(argv):
    limit = DEFAULT_LIMIT
    only = []
    i = 0
    while i < len(argv):
        if argv[i] == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1]); i += 2
        else:
            only.append(argv[i]); i += 1

    if not settings.ALPHAVANTAGE_API_KEY:
        log.error("ALPHAVANTAGE_API_KEY 미설정 — .env에 키를 추가하세요. "
                  "무료 발급: https://www.alphavantage.co/support/#api-key")
        return 1

    db = SessionLocal()
    try:
        tickers = _target_tickers(db, only)
        if not tickers:
            log.warning("대상 종목 없음")
            return 0
        log.info(f"백필 대상 {len(tickers)}종목(미국 관심권), 이번 실행 최대 {limit}개")

        processed = ok = skipped = 0
        for t in tickers:
            stock = db.query(Stock).filter(Stock.ticker == t).first()
            if not only and stock and not _needs_backfill(db, stock.id):
                skipped += 1
                continue
            if processed >= limit:
                log.info(f"이번 실행 한도({limit}) 도달 — 중단. 다음에 이어서 실행하세요.")
                break

            success, msg = backfill_one(db, t)
            processed += 1
            if success:
                ok += 1
                log.info(f"[{processed}/{limit}] {t}: {msg}")
            else:
                if msg and msg.startswith("LIMIT:"):
                    log.warning(f"{t}: Alpha Vantage 일일 한도 도달 → 중단({msg[6:].strip()}). "
                                "다음날 같은 명령으로 이어서 실행하세요.")
                    break
                log.warning(f"[{processed}/{limit}] {t}: 실패 — {msg}")
            time.sleep(SLEEP_SEC)

        log.info(f"완료: 처리 {processed}, 성공 {ok}, 이미완료 건너뜀 {skipped}")
        log.info("이제 'python3 scripts/recompute_yoy.py'로 YoY를 채우세요.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
