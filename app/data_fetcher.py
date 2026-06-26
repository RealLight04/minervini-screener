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
            {"ticker": row["Symbol"].replace(".", "-"), "name": row["Security"],
             "sector": row["GICS Sector"], "market": "US"}
            for _, row in df.iterrows()
        ]
    except Exception as e:
        logger.error(f"S&P 500 목록 수집 실패: {e}")
        return []


def fetch_kr_tickers(kospi_n: int = 200, kosdaq_n: int = 100) -> list[dict]:
    """
    한국(코스피/코스닥) 종목 목록 — FinanceDataReader로 받아 시가총액 상위 N개 선별.
    yfinance 형식 티커(.KS=코스피, .KQ=코스닥)로 변환해 반환.
    """
    try:
        import FinanceDataReader as fdr
    except ImportError:
        logger.error("FinanceDataReader 미설치 — 한국 종목 수집 불가")
        return []

    result = []
    for market, suffix, top_n in [("KOSPI", "KS", kospi_n), ("KOSDAQ", "KQ", kosdaq_n)]:
        try:
            df = fdr.StockListing(market)
            if "Marcap" in df.columns:
                df = df.sort_values("Marcap", ascending=False)
            # 우선주(코드 끝자리 0 아님)·SPAC 등 제외: 보통주 위주(코드가 ...0 으로 끝)
            df = df[df["Code"].astype(str).str.match(r"^\d{6}$")]
            df = df[df["Code"].astype(str).str.endswith("0")]
            for _, row in df.head(top_n).iterrows():
                code = str(row["Code"])
                result.append({
                    "ticker": f"{code}.{suffix}",
                    "name": row.get("Name"),
                    # FDR 'Dept'는 거래소 소속부(상장 등급)일 뿐 업종이 아니므로 sector로 쓰지 않는다.
                    # 실섹터는 scripts/collect_kr_sectors.py(yfinance)로 백필. (테마 오염 방지)
                    "sector": None,
                    "market": market,
                })
        except Exception as e:
            logger.error(f"{market} 목록 수집 실패: {e}")
    return result


def ensure_stocks_in_db(db: Session, stock_list: list[dict]) -> None:
    """종목이 DB에 없으면 추가 (배치 내 중복 티커도 안전하게 처리). market도 갱신."""
    existing = {t: m for (t, m) in db.query(Stock.ticker, Stock.market).all()}
    seen = set()
    for item in stock_list:
        ticker = item["ticker"]
        market = item.get("market", "US")
        if ticker in seen:
            continue
        seen.add(ticker)
        if ticker in existing:
            # 기존 종목의 market이 비어있으면 보정
            if not existing[ticker]:
                st = db.query(Stock).filter(Stock.ticker == ticker).first()
                if st:
                    st.market = market
            continue
        db.add(Stock(ticker=ticker, name=item["name"], sector=item.get("sector"), market=market))
    db.commit()


MIN_LISTING_DAYS = 365  # 상장 후 최소 경과일 — 미만이면 비활성화


def fetch_and_save_prices(db: Session, ticker: str, period_days: int = 450) -> bool:
    """주가 데이터 수집 및 저장 (최근 N일)

    수집 창(450일)을 screener.prune_old_history의 보존 창(450일)과 맞춰
    매일 같은 구간이 추가·삭제되는 churn을 없앤다. 스크리너 최대 lookback(RS 52주
    ≈ 374일)보다 넉넉하므로 신호에 영향 없음.
    """
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if not stock:
        return False
    if not stock.is_active:
        return False

    start = date.today() - timedelta(days=period_days)
    try:
        yf_ticker = yf.Ticker(ticker)
        hist = yf_ticker.history(start=start.isoformat(), auto_adjust=True)
        if hist.empty:
            return False

        # 상장 1년 미만: 스크리너 lookback(52주)을 못 채우므로 비활성화
        first_date = hist.index[0].date() if hasattr(hist.index[0], "date") else hist.index[0].to_pydatetime().date()
        if (date.today() - first_date).days < MIN_LISTING_DAYS:
            stock.is_active = False
            db.commit()
            logger.info(f"{ticker} 상장 {(date.today() - first_date).days}일 → is_active=False")
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
    if not stock.is_active:
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
            opi_row = _pick_row(stmt, ["Operating Income", "Total Operating Income As Reported"])
            if eps_row is None and rev_row is None:
                continue

            # 컬럼(기간)을 과거→현재 순으로 정렬
            periods = sorted(stmt.columns)
            eps_by_period: dict = {}
            rev_by_period: dict = {}
            opi_by_period: dict = {}
            for p in periods:
                eps_by_period[p] = _safe_val(eps_row, p)
                rev_by_period[p] = _safe_val(rev_row, p)
                opi_by_period[p] = _safe_val(opi_row, p)

            # YoY 비교 기준 기간을 '날짜로' 매칭 (분기 누락이 있어도 정확). 약 1년 전 ±45일
            tol = 45 if period_type == "Q" else 120
            target_days = 365

            def _yoy_partner(p):
                want = p - pd.Timedelta(days=target_days)
                best, bestdiff = None, tol + 1
                for q in periods:
                    diff = abs((q - want).days)
                    if diff <= tol and diff < bestdiff:
                        best, bestdiff = q, diff
                return best

            for p in periods:
                period_date = p.date() if hasattr(p, "date") else None
                if period_date is None:
                    continue

                eps = eps_by_period[p]
                revenue = rev_by_period[p]
                opi = opi_by_period[p]
                op_margin = (opi / revenue * 100) if (opi is not None and revenue not in (None, 0)) else None

                eps_growth = rev_growth = opi_growth = None
                prev_p = _yoy_partner(p)
                if prev_p is not None:
                    prev_eps = eps_by_period.get(prev_p)
                    prev_rev = rev_by_period.get(prev_p)
                    prev_opi = opi_by_period.get(prev_p)
                    if eps is not None and prev_eps not in (None, 0):
                        eps_growth = (eps - prev_eps) / abs(prev_eps) * 100
                    if revenue is not None and prev_rev not in (None, 0):
                        rev_growth = (revenue - prev_rev) / abs(prev_rev) * 100
                    if opi is not None and prev_opi not in (None, 0):
                        opi_growth = (opi - prev_opi) / abs(prev_opi) * 100

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
                    existing.eps = eps
                    existing.revenue = revenue
                    existing.operating_income = opi
                    existing.operating_margin = round(op_margin, 2) if op_margin is not None else None
                    existing.eps_growth_yoy = eps_growth
                    existing.revenue_growth_yoy = rev_growth
                    existing.operating_income_growth_yoy = opi_growth
                else:
                    db.add(
                        Fundamental(
                            stock_id=stock.id,
                            period_type=period_type,
                            period_date=period_date,
                            eps=eps,
                            revenue=revenue,
                            operating_income=opi,
                            operating_margin=round(op_margin, 2) if op_margin is not None else None,
                            eps_growth_yoy=eps_growth,
                            revenue_growth_yoy=rev_growth,
                            operating_income_growth_yoy=opi_growth,
                        )
                    )
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"{ticker} 재무 수집 실패: {e}")
        db.rollback()
        return False


_DART_REV = ["매출액", "수익(매출액)", "영업수익", "매출"]
_DART_OPI = ["영업이익", "영업이익(손실)"]
# 분기 → (보고서코드, 월, 일). 11011(사업보고서)=연간 → Q4는 연간-(Q1+Q2+Q3)
_DART_QMAP = {1: ("11013", 3, 31), 2: ("11012", 6, 30), 3: ("11014", 9, 30), 4: ("11011", 12, 31)}


def _dart_val(fs, candidates: list[str]):
    """DART finstate DataFrame에서 연결>별도 우선으로 계정 금액 추출"""
    if not isinstance(fs, pd.DataFrame) or "fs_nm" not in fs.columns:
        return None
    for fsname in ["연결재무제표", "재무제표"]:
        for c in candidates:
            row = fs[(fs["fs_nm"] == fsname) & (fs["account_nm"] == c)]
            if len(row):
                try:
                    return int(str(row.iloc[0]["thstrm_amount"]).replace(",", ""))
                except Exception:
                    pass
    return None


def fetch_dart_quarterly(db: Session, dart, ticker: str, years: list[int]) -> bool:
    """
    OpenDART에서 한국 종목의 단일분기 매출·영업이익 수집 → Fundamental(Q) 저장.
    분기보고서(11013/11012/11014)는 단일분기, 사업보고서(11011)는 연간 → Q4 = 연간-(Q1~Q3).
    """
    from datetime import date

    code = ticker.split(".")[0]
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if not stock:
        return False

    got = 0
    for year in years:
        vals: dict = {}
        for q, (rc, _, _) in _DART_QMAP.items():
            try:
                fs = dart.finstate(code, year, reprt_code=rc)
            except Exception:
                fs = None
            rev = _dart_val(fs, _DART_REV)
            if rev is not None:
                vals[q] = {"rev": rev, "opi": _dart_val(fs, _DART_OPI)}

        # Q4(단일) = 연간 - (Q1+Q2+Q3)
        if 4 in vals and all(q in vals for q in (1, 2, 3)):
            for k in ("rev", "opi"):
                if all(vals[q].get(k) is not None for q in (1, 2, 3, 4)):
                    vals[4][k] = vals[4][k] - sum(vals[q][k] for q in (1, 2, 3))

        for q, v in vals.items():
            if v["rev"] is None:
                continue
            _, mm, dd = _DART_QMAP[q]
            pdate = date(year, mm, dd)
            rev = float(v["rev"])
            opi = float(v["opi"]) if v["opi"] is not None else None
            margin = round(opi / rev * 100, 2) if (opi is not None and rev) else None
            existing = (
                db.query(Fundamental)
                .filter(
                    Fundamental.stock_id == stock.id,
                    Fundamental.period_type == "Q",
                    Fundamental.period_date == pdate,
                )
                .first()
            )
            if existing:
                existing.revenue = rev
                existing.operating_income = opi
                existing.operating_margin = margin
            else:
                db.add(Fundamental(
                    stock_id=stock.id, period_type="Q", period_date=pdate,
                    revenue=rev, operating_income=opi, operating_margin=margin,
                ))
            got += 1
    db.commit()
    return got > 0


def fetch_company_info(db: Session, ticker: str) -> bool:
    """yfinance .info에서 기업 기본정보(ROE·마진·목표주가·투자의견·다음 실적일) 수집."""
    from datetime import datetime, timezone

    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if not stock:
        return False
    try:
        info = yf.Ticker(ticker).info or {}
        stock.roe = info.get("returnOnEquity")
        stock.profit_margin = info.get("profitMargins")
        stock.operating_margin = info.get("operatingMargins")
        stock.forward_eps = info.get("forwardEps")
        stock.trailing_eps = info.get("trailingEps")
        stock.target_price = info.get("targetMeanPrice")
        stock.recommendation = info.get("recommendationKey")
        ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
        if ts:
            try:
                stock.next_earnings = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            except Exception:
                pass
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"{ticker} 기업정보 수집 실패: {e}")
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
