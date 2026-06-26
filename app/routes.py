from datetime import date
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Fundamental, ScreeningResult, Stock
from app.screener import SIGNAL_LABELS, build_trade_plan, compute_market_breadth

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# 템플릿에서 신호 한국어 라벨/색상 사용
SIGNAL_COLORS = {
    "STRONG_BUY": "#22c55e",
    "BUY": "#4ade80",
    "WATCH": "#94a3b8",
    "SELL": "#f97316",
    "AVOID": "#64748b",
}
templates.env.globals["signal_labels"] = SIGNAL_LABELS
templates.env.globals["signal_colors"] = SIGNAL_COLORS

MARKETS = ["US", "KOSPI", "KOSDAQ"]
MARKET_LABELS = {"US": "미국 (S&P 500)", "KOSPI": "코스피", "KOSDAQ": "코스닥"}


def fmt_price(value, market: str = "US") -> str:
    """시장별 주가 포맷: 미국=$x.xx, 한국=₩x (원화는 소수점 없음)"""
    if value is None:
        return "-"
    return f"${value:,.2f}" if market == "US" else f"₩{value:,.0f}"


def fmt_amount(value, market: str = "US") -> str:
    """시장별 금액(계좌/포지션) 포맷 — 소수점 없음"""
    if value is None:
        return "-"
    return f"${value:,.0f}" if market == "US" else f"₩{value:,.0f}"


REC_LABELS = {
    "strong_buy": "적극매수", "buy": "매수", "hold": "중립",
    "underperform": "비중축소", "sell": "매도",
}

templates.env.globals["fmt_price"] = fmt_price
templates.env.globals["fmt_amount"] = fmt_amount
templates.env.globals["market_labels"] = MARKET_LABELS
templates.env.globals["rec_labels"] = REC_LABELS


def _available_markets(db: Session) -> list[str]:
    """스크리닝 결과가 있는 시장 목록 (탭 렌더용)"""
    rows = {m for (m,) in db.query(Stock.market).filter(Stock.is_active == True).distinct().all()}
    return [m for m in MARKETS if m in rows]


def _latest_screen_date(db: Session) -> date | None:
    row = (
        db.query(ScreeningResult.screen_date)
        .order_by(ScreeningResult.screen_date.desc())
        .first()
    )
    return row[0] if row else None


@router.get("/", response_class=HTMLResponse)
def index(request: Request, market: str = "US", db: Session = Depends(get_db)):
    # 데이터가 있는 가장 최근 스크리닝 날짜 사용
    screen_date = _latest_screen_date(db) or date.today()

    avail = _available_markets(db) or ["US"]
    if market not in avail:
        market = avail[0]

    def _by_signals(signals):
        return (
            db.query(ScreeningResult, Stock)
            .join(Stock, ScreeningResult.stock_id == Stock.id)
            .filter(
                ScreeningResult.screen_date == screen_date,
                ScreeningResult.signal.in_(signals),
                Stock.market == market,
            )
            .order_by(ScreeningResult.rs_rank.desc())
            .all()
        )

    # 매수 후보: 적극매수 우선, 그 다음 매수 (RS 순)
    buy_list = _by_signals(["STRONG_BUY", "BUY"])
    buy_list.sort(key=lambda rs: (rs[0].signal != "STRONG_BUY", -(rs[0].rs_rank or 0)))

    # 돌파 대기: 피벗 아래에서 코일링 중(매수가 확정) → 돌파 임박 순(피벗까지 가까운 순) 정렬
    breakout_watch = []
    for r, s in buy_list:
        if r.pivot_price and r.close and r.close < r.pivot_price:
            gap = round((r.pivot_price / r.close - 1) * 100, 1)
            breakout_watch.append((r, s, gap))
    breakout_watch.sort(key=lambda x: x[2])

    # 매도 경고: Stage 2 유지 중 50일선 이탈 종목 (RS 강한 순 상위 30개만 표시)
    sell_all = _by_signals(["SELL"])
    sell_list = sell_all[:30]

    # 시장 국면(breadth) — 선택한 시장 기준
    breadth = compute_market_breadth(db, screen_date, market=market)

    # 주도 섹터/테마: 섹터별 매수후보 수·Stage2 비율·평균 RS 집계
    srows = (
        db.query(Stock.sector, ScreeningResult.signal,
                 ScreeningResult.technical_pass, ScreeningResult.rs_rank)
        .join(ScreeningResult, ScreeningResult.stock_id == Stock.id)
        .filter(ScreeningResult.screen_date == screen_date, Stock.market == market)
        .all()
    )
    sec_agg: dict = {}
    for sec, sig, tech, rs in srows:
        if not sec:
            continue
        a = sec_agg.setdefault(sec, {"total": 0, "buy": 0, "stage2": 0, "rs_sum": 0.0, "rs_n": 0})
        a["total"] += 1
        if sig in ("BUY", "STRONG_BUY"):
            a["buy"] += 1
        if tech:
            a["stage2"] += 1
        if rs is not None:
            a["rs_sum"] += rs
            a["rs_n"] += 1
    themes = []
    for sec, a in sec_agg.items():
        if a["total"] < 3:
            continue
        themes.append({
            "sector": sec,
            "total": a["total"],
            "buy": a["buy"],
            "stage2_pct": round(a["stage2"] / a["total"] * 100),
            "avg_rs": round(a["rs_sum"] / a["rs_n"]) if a["rs_n"] else 0,
        })
    themes.sort(key=lambda x: (-x["buy"], -x["stage2_pct"], -x["avg_rs"]))
    themes = themes[:6]

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "buy_list": buy_list,
            "breakout_watch": breakout_watch,
            "themes": themes,
            "sell_list": sell_list,
            "screen_date": screen_date,
            "buy_count": len(buy_list),
            "sell_count": len(sell_all),
            "market": breadth,
            "cur_market": market,
            "avail_markets": avail,
        },
    )


@router.get("/search")
def search(ticker: str = "", db: Session = Depends(get_db)):
    """헤더 검색 → 종목 상세로 리다이렉트"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/stock/{ticker.strip().upper()}", status_code=302)


@router.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_detail(ticker: str, request: Request, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(Stock.ticker == ticker.upper()).first()
    if not stock:
        return HTMLResponse("<h2>종목을 찾을 수 없습니다.</h2>", status_code=404)

    latest_result = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.stock_id == stock.id)
        .order_by(ScreeningResult.screen_date.desc())
        .first()
    )

    # 최근 30일 스크리닝 이력
    history = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.stock_id == stock.id)
        .order_by(ScreeningResult.screen_date.desc())
        .limit(30)
        .all()
    )

    # 최근 분기/연간 실적 (오래된→최신 순으로 정렬해 추이 표시)
    q_funds = (
        db.query(Fundamental)
        .filter(Fundamental.stock_id == stock.id, Fundamental.period_type == "Q")
        .order_by(Fundamental.period_date.desc())
        .limit(5)
        .all()
    )[::-1]
    y_funds = (
        db.query(Fundamental)
        .filter(Fundamental.stock_id == stock.id, Fundamental.period_type == "Y")
        .order_by(Fundamental.period_date.desc())
        .limit(3)
        .all()
    )[::-1]

    # 분기 성장률 기준은 '지표별로 독립' 판정:
    #  - 그 지표의 YoY가 3분기 이상 있으면 YoY(정석) — 한국=DART, 미국 EPS=Alpha Vantage 백필
    #  - 부족하면 QoQ 폴백 — 미국 매출·영업이익(yfinance 무료는 ~5분기라 YoY 3개치 불가)
    # (예: 미국 종목은 EPS만 YoY, 매출·영업이익은 QoQ가 될 수 있음)
    def _qoq(attr):
        out, prev = [], None
        for f in q_funds:
            cur = getattr(f, attr)
            g = round((cur - prev) / abs(prev) * 100, 1) if (cur is not None and prev not in (None, 0)) else None
            out.append(g)
            if cur is not None:
                prev = cur
        return out

    def _metric(raw_attr, yoy_attr):
        yoy = [getattr(f, yoy_attr) for f in q_funds]
        if len([x for x in yoy if x is not None]) >= 3:
            return yoy, "YoY"
        return _qoq(raw_attr), "QoQ"

    rev_g, rev_basis = _metric("revenue", "revenue_growth_yoy")
    opi_g, opi_basis = _metric("operating_income", "operating_income_growth_yoy")
    eps_g, eps_basis = _metric("eps", "eps_growth_yoy")
    basis = {"revenue": rev_basis, "operating": opi_basis, "eps": eps_basis}
    # 표 전체 안내문구용 대표 기준(하나라도 YoY면 YoY 우대 표기)
    growth_basis = "YoY" if "YoY" in basis.values() else "QoQ"

    def _accel3(values):  # 최근 3개 값이 연속 증가(가속/확대)
        v = [x for x in values if x is not None][-3:]
        return len(v) == 3 and v[0] < v[1] < v[2]

    accel = {
        "revenue": _accel3(rev_g),
        "operating": _accel3(opi_g),
        "margin": _accel3([f.operating_margin for f in q_funds]),
        "eps": _accel3(eps_g),
    }
    eps_accelerating = accel["eps"]  # 기존 호환
    # Code 33: 매출·영업이익이 동시에 3분기 연속 가속 = 전방위 실적 모멘텀 (미너비니 최상급)
    code33 = accel["revenue"] and accel["operating"]

    # EPS 연속 성장 streak: 최신 분기부터 YoY가 양(+)으로 끊기지 않고 이어진 분기 수.
    # '가속'(증가율이 매분기 커짐)과 다른, '연속 성장' 개념(미너비니 핵심 점검 항목).
    # YoY 기준일 때만 의미가 있다(QoQ는 계절성 때문에 연속성 판단 부적합).
    eps_streak = 0
    if eps_basis == "YoY":
        for v in reversed(eps_g):
            if v is not None and v > 0:
                eps_streak += 1
            else:
                break

    q_rows = [{
        "date": f.period_date,
        "rev": rev_g[i],
        "opi": opi_g[i],
        "margin": f.operating_margin,
        "eps": eps_g[i],
        "surprise": f.eps_surprise_pct,
    } for i, f in enumerate(q_funds)]

    # 어닝 서프라이즈(미국, Alpha Vantage): 최근 분기 서프라이즈 + 연속 비트(beat) 횟수.
    # 추정치를 웃돌면(>0) 'beat'. 미너비니는 어닝 서프라이즈를 강세 모멘텀 신호로 본다.
    eps_surprise_latest = next((f.eps_surprise_pct for f in reversed(q_funds)
                                if f.eps_surprise_pct is not None), None)
    beat_streak = 0
    for f in reversed(q_funds):
        if f.eps_surprise_pct is None:
            break
        if f.eps_surprise_pct > 0:
            beat_streak += 1
        else:
            break

    trade_plan = build_trade_plan(latest_result, stock.market or "US") if latest_result else None

    return templates.TemplateResponse(
        request,
        "stock.html",
        context={
            "stock": stock,
            "result": latest_result,
            "history": history,
            "q_funds": q_funds,
            "q_rows": q_rows,
            "growth_basis": growth_basis,
            "basis": basis,
            "y_funds": y_funds,
            "eps_accelerating": eps_accelerating,
            "eps_streak": eps_streak,
            "accel": accel,
            "code33": code33,
            "eps_surprise_latest": eps_surprise_latest,
            "beat_streak": beat_streak,
            "trade_plan": trade_plan,
        },
    )


@router.get("/api/screen-now")
def trigger_screen(db: Session = Depends(get_db)):
    """수동 스크리닝 트리거 (개발/테스트용)"""
    from app.screener import run_daily_screen
    passed = run_daily_screen(db)
    return {"status": "ok", "passed": passed, "date": str(date.today())}


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist(request: Request):
    """관심종목 페이지 (목록은 브라우저 localStorage에 저장 → JS가 채움)"""
    return templates.TemplateResponse(request, "watchlist.html", context={})


@router.get("/api/quote")
def quote(tickers: str = "", db: Session = Depends(get_db)):
    """관심종목용 요약: 콤마구분 티커들의 신호/현재가/RS/피벗."""
    tks = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not tks:
        return []
    stocks = db.query(Stock).filter(Stock.ticker.in_(tks)).all()
    out = []
    for s in stocks:
        r = (
            db.query(ScreeningResult)
            .filter(ScreeningResult.stock_id == s.id)
            .order_by(ScreeningResult.screen_date.desc())
            .first()
        )
        mkt = s.market or "US"
        gap = None
        if r and r.pivot_price and r.close and r.close < r.pivot_price:
            gap = round((r.pivot_price / r.close - 1) * 100, 1)
        out.append({
            "ticker": s.ticker,
            "name": s.name,
            "market": mkt,
            "currency": "$" if mkt == "US" else "₩",
            "signal": r.signal if r else None,
            "signal_label": SIGNAL_LABELS.get(r.signal, r.signal) if (r and r.signal) else "-",
            "signal_color": SIGNAL_COLORS.get(r.signal, "#94a3b8") if (r and r.signal) else "#94a3b8",
            "close": r.close if r else None,
            "rs_rank": round(r.rs_rank) if (r and r.rs_rank is not None) else None,
            "pivot": r.pivot_price if r else None,
            "gap_to_pivot": gap,
        })
    order = {t: i for i, t in enumerate(tks)}
    out.sort(key=lambda x: order.get(x["ticker"], 999))
    return out


@router.get("/api/chart/{ticker}")
def chart_data(ticker: str, db: Session = Depends(get_db)):
    """차트용 시계열: 최근 1년 종가 + 이동평균선(50/150/200) + 거래량 + 피벗/손절."""
    import pandas as pd
    from app.models import DailyPrice

    stock = db.query(Stock).filter(Stock.ticker == ticker.upper()).first()
    if not stock:
        return {"error": "not found"}

    # 200일선 계산 위해 전체를 받아 이동평균 계산 후 마지막 250개만 표시
    rows = (
        db.query(DailyPrice.date, DailyPrice.open, DailyPrice.high,
                 DailyPrice.low, DailyPrice.close, DailyPrice.volume)
        .filter(DailyPrice.stock_id == stock.id)
        .order_by(DailyPrice.date)
        .all()
    )
    if not rows:
        return {"error": "no data"}

    close = pd.Series([r[4] for r in rows], dtype=float)
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()

    def tail(seq, n=250):
        return list(seq)[-n:]

    def r2(v):
        return None if (v is None or pd.isna(v)) else round(float(v), 2)

    latest = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.stock_id == stock.id)
        .order_by(ScreeningResult.screen_date.desc())
        .first()
    )

    # lightweight-charts용 캔들/거래량/이동평균 (time = YYYY-MM-DD)
    candles, vols, ma50_s, ma150_s, ma200_s = [], [], [], [], []
    n = len(rows)
    for i in range(max(0, n - 250), n):
        d = rows[i][0].isoformat()
        candles.append({"time": d, "open": r2(rows[i][1]), "high": r2(rows[i][2]),
                        "low": r2(rows[i][3]), "close": r2(rows[i][4])})
        up = (rows[i][4] or 0) >= (rows[i][1] or 0)
        vols.append({"time": d, "value": int(rows[i][5] or 0),
                     "color": "rgba(74,222,128,0.35)" if up else "rgba(248,113,113,0.35)"})
        if not pd.isna(ma50.iloc[i]):
            ma50_s.append({"time": d, "value": round(float(ma50.iloc[i]), 2)})
        if not pd.isna(ma150.iloc[i]):
            ma150_s.append({"time": d, "value": round(float(ma150.iloc[i]), 2)})
        if not pd.isna(ma200.iloc[i]):
            ma200_s.append({"time": d, "value": round(float(ma200.iloc[i]), 2)})

    # 매수 지점: 돌파형=피벗, 눌림형=50일선 (매수 신호일 때만). 손절은 매수가 -8%
    buy, buy_label = None, None
    chart_stop = latest.stop_loss if latest else None
    if latest and latest.signal in ("BUY", "STRONG_BUY"):
        if latest.pivot_price:
            buy, buy_label = latest.pivot_price, "🎯 매수(피벗 돌파)"
            chart_stop = round(latest.pivot_price * 0.92, 2)
        elif latest.ma50:
            buy, buy_label = round(latest.ma50, 2), "🎯 매수(50일선 눌림)"
            chart_stop = round(latest.ma50 * 0.92, 2)

    return {
        "ticker": stock.ticker,
        "name": stock.name,
        "market": stock.market or "US",
        "currency": "$" if (stock.market or "US") == "US" else "₩",
        "candles": candles,
        "volume": vols,
        "ma50": ma50_s,
        "ma150": ma150_s,
        "ma200": ma200_s,
        "pivot": latest.pivot_price if latest else None,
        "stop": chart_stop,
        "buy": buy,
        "buy_label": buy_label,
    }


@router.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    # 인덱스와 동일하게 '데이터가 있는 최신 스크리닝일' 기준 (배포 스냅샷이 과거일 수 있음)
    screen_date = _latest_screen_date(db) or date.today()
    total = db.query(ScreeningResult).filter(ScreeningResult.screen_date == screen_date).count()
    tech_pass = db.query(ScreeningResult).filter(
        ScreeningResult.screen_date == screen_date, ScreeningResult.technical_pass == True
    ).count()
    final_pass = db.query(ScreeningResult).filter(
        ScreeningResult.screen_date == screen_date, ScreeningResult.final_pass == True
    ).count()
    return {"date": str(screen_date), "total": total, "technical_pass": tech_pass, "final_pass": final_pass}
