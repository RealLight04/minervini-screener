"""Yahoo Finance를 이용한 주가 및 재무 데이터 수집"""
import logging
from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf
from sqlalchemy.orm import Session

from app.models import DailyPrice, Fundamental, Stock

logger = logging.getLogger(__name__)

# S&P 500 종목 목록 (Wikipedia에서 수집)
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_tickers() -> list[dict]:
    """S&P 500 종목 목록 가져오기 (Wikipedia는 User-Agent 필요)"""
    try:
        import io
        headers = {"User-Agent": "Mozilla/5.0 (stock-screener; +https://github.com)"}
        resp = requests.get(SP500_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        df = tables[0]
        return [
            {"ticker": row["Symbol"].replace(".", "-"), "name": row["Security"], "sector": row["GICS Sector"]}
            for _, row in df.iterrows()
        ]
    except Exception as e:
        logger.error(f"S&P 500 목록 수집 실패: {e}")
        return []


def ensure_stocks_in_db(db: Session, stock_list: list[dict]) -> None:
    """종목이 DB에 없으면 추가 (배치 내 중복 티커도 안전하게 처리)"""
    existing_tickers = {t for (t,) in db.query(Stock.ticker).all()}
    seen = set()
    for item in stock_list:
        ticker = item["ticker"]
        if ticker in existing_tickers or ticker in seen:
            continue
        seen.add(ticker)
        db.add(Stock(ticker=ticker, name=item["name"], sector=item.get("sector")))
    db.commit()


def fetch_and_save_prices(db: Session, ticker: str, period_days: int = 500) -> bool:
    """주가 데이터 수집 및 저장 (최근 N일)"""
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if not stock:
        return False

    start = date.today() - timedelta(days=period_days)
    try:
        yf_ticker = yf.Ticker(ticker)
        hist = yf_ticker.history(start=start.isoformat(), auto_adjust=True)
        if hist.empty:
            return False

        for idx, row in hist.iterrows():
            price_date = idx.date()
            # 이미 있으면 건너뜀
            exists = (
                db.query(DailyPrice)
                .filter(DailyPrice.stock_id == stock.id, DailyPrice.date == price_date)
                .first()
            )
            if not exists:
                db.add(
                    DailyPrice(
                        stock_id=stock.id,
                        date=price_date,
                        open=row.get("Open"),
                        high=row.get("High"),
                        low=row.get("Low"),
                        close=row["Close"],
                        volume=int(row.get("Volume", 0)),
                    )
                )
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"{ticker} 주가 수집 실패: {e}")
        db.rollback()
        return False


def fetch_and_save_fundamentals(db: Session, ticker: str) -> bool:
    """분기/연간 EPS·매출 수집 및 저장"""
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if not stock:
        return False

    try:
        yf_ticker = yf.Ticker(ticker)

        # 신버전 yfinance는 income_stmt에 EPS·매출이 함께 들어있음
        # (구버전 .earnings / .quarterly_earnings는 None 반환 → 사용 불가)
        for period_type, stmt in [
            ("Q", yf_ticker.quarterly_income_stmt),
            ("Y", yf_ticker.income_stmt),
        ]:
            if stmt is None or stmt.empty:
                continue

            eps_row = _pick_row(stmt, ["Diluted EPS", "Basic EPS"])
            rev_row = _pick_row(stmt, ["Total Revenue", "Operating Revenue"])
            if eps_row is None and rev_row is None:
                continue

            # 컬럼(기간)을 과거→현재 순으로 정렬
            periods = sorted(stmt.columns)
            eps_by_period: dict = {}
            rev_by_period: dict = {}
            for p in periods:
                eps_by_period[p] = _safe_val(eps_row, p)
                rev_by_period[p] = _safe_val(rev_row, p)

            yoy_offset = 4 if period_type == "Q" else 1
            for i, p in enumerate(periods):
                period_date = p.date() if hasattr(p, "date") else None
                if period_date is None:
                    continue

                eps = eps_by_period[p]
                revenue = rev_by_period[p]

                # YoY 성장률 (4분기 전 / 1년 전과 비교)
                eps_growth = None
                rev_growth = None
                if i >= yoy_offset:
                    prev_p = periods[i - yoy_offset]
                    prev_eps = eps_by_period.get(prev_p)
                    prev_rev = rev_by_period.get(prev_p)
                    if eps is not None and prev_eps not in (None, 0):
                        eps_growth = (eps - prev_eps) / abs(prev_eps) * 100
                    if revenue is not None and prev_rev not in (None, 0):
                        rev_growth = (revenue - prev_rev) / abs(prev_rev) * 100

                existing = (
                    db.query(Fundamental)
                    .filter(
                        Fundamental.stock_id == stock.id,
                        Fundamental.period_type == period_type,
                        Fundamental.period_date == period_date,
                    )
                    .first()
                )
                if existing:
                    # 최신 계산값으로 갱신
                    existing.eps = eps
                    existing.revenue = revenue
                    existing.eps_growth_yoy = eps_growth
                    existing.revenue_growth_yoy = rev_growth
                else:
                    db.add(
                        Fundamental(
                            stock_id=stock.id,
                            period_type=period_type,
                            period_date=period_date,
                            eps=eps,
                            revenue=revenue,
                            eps_growth_yoy=eps_growth,
                            revenue_growth_yoy=rev_growth,
                        )
                    )
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"{ticker} 재무 수집 실패: {e}")
        db.rollback()
        return False


def _pick_row(stmt, candidates: list[str]):
    """income statement에서 후보 행 이름 중 존재하는 첫 번째 행 반환"""
    for name in candidates:
        if name in stmt.index:
            return stmt.loc[name]
    return None


def _safe_val(row, period):
    """행에서 특정 기간 값을 안전하게 float로 추출 (NaN → None)"""
    if row is None:
        return None
    try:
        val = row.get(period)
        if val is None or pd.isna(val):
            return None
        return float(val)
    except Exception:
        return None
