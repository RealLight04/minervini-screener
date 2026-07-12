from datetime import date, timedelta
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import alerts as alerts_mod
from app.database import get_db
from app.models import DailyPrice, Fundamental, ScreeningResult, Stock
from app.screener import SIGNAL_LABELS, build_trade_plan, compute_market_breadth

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# 템플릿에서 신호 한국어 라벨/색상 사용
SIGNAL_COLORS = {
    "STRONG_BUY": {"light": "#166534", "dark": "#22c55e"},
    "BUY": {"light": "#15803d", "dark": "#4ade80"},
    "WATCH": {"light": "#52514e", "dark": "#8b96a8"},
    "SELL": {"light": "#c2410c", "dark": "#fb923c"},
    "AVOID": {"light": "#898781", "dark": "#596177"},
}
SIGNAL_COLOR_DEFAULT = {"light": "#52514e", "dark": "#8b96a8"}
templates.env.globals["signal_labels"] = SIGNAL_LABELS
templates.env.globals["signal_colors"] = SIGNAL_COLORS
templates.env.globals["signal_color_default"] = SIGNAL_COLOR_DEFAULT

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


def tv_url(ticker: str, market: str = "US") -> str:
    """TradingView 차트 링크: 한국 종목은 .KS/.KQ 접미사를 떼고 KRX- 접두사로 변환"""
    if market == "US":
        return f"https://www.tradingview.com/symbols/{ticker}/"
    code = ticker.split(".")[0]
    return f"https://www.tradingview.com/symbols/KRX-{code}/"


REC_LABELS = {
    "strong_buy": "적극매수", "buy": "매수", "hold": "중립",
    "underperform": "비중축소", "sell": "매도",
}

def fmt_turnover(avg_volume, close, market: str = "US") -> str:
    """거래대금(≈평균거래량×현재가) = 유동성 규모. 미국 $M/$B, 한국 억/조원."""
    if not avg_volume or not close:
        return "-"
    val = avg_volume * close
    if market == "US":
        if val >= 1e9:
            return f"${val/1e9:.1f}B"
        if val >= 1e6:
            return f"${val/1e6:.0f}M"
        return f"${val:,.0f}"
    if val >= 1e12:
        return f"{val/1e12:.1f}조원"
    if val >= 1e8:
        return f"{val/1e8:,.0f}억원"
    return f"{val:,.0f}원"


def ad_interpret(accum, distrib) -> dict:
    """최근 25일 매집(accum)/분산(distrib) 일수 → 해석. 미너비니: 대량거래일의 방향이 기관 의중.

    매집일 = 대량거래 + 상승 마감(기관이 사들인 흔적)
    분산일 = 대량거래 + 하락 마감(기관이 판 흔적)
    """
    GRAY = {"light": "#52514e", "dark": "#8b96a8"}
    GREEN = {"light": "#15803d", "dark": "#4ade80"}
    RED = {"light": "#b91c1c", "dark": "#f87171"}
    a, d = accum or 0, distrib or 0
    if a == 0 and d == 0:
        return {"verdict": "자료 부족", "color": GRAY, "accum": a, "distrib": d,
                "note": "대량거래일이 뚜렷하지 않습니다."}
    if d >= 5 and d >= a:
        return {"verdict": "분산 우세", "color": RED, "accum": a, "distrib": d,
                "note": "대량 하락일이 많습니다 — 기관 매도 흔적. 신규 매수 신중, 보유 시 경계하세요."}
    if a >= d + 2:
        return {"verdict": "매집 우세", "color": GREEN, "accum": a, "distrib": d,
                "note": "대량 상승일 우세 — 기관 매수 흔적(건강), 미너비니 선호 패턴."}
    if d >= a + 2:
        return {"verdict": "분산 우세", "color": RED, "accum": a, "distrib": d,
                "note": "대량 하락일 우세 — 기관 매도 압력, 추세 약화 주의."}
    return {"verdict": "중립", "color": GRAY, "accum": a, "distrib": d,
            "note": "매집·분산이 팽팽합니다 — 대량거래 동반 돌파를 기다리세요."}


def volume_verdict(r) -> dict:
    """종목의 '현재 국면 + 거래량'을 조합해 거래량이 지금 건강한지 한 줄로 판정.

    미너비니 원칙: 오를 땐/돌파할 땐 거래량 많아야 좋고, 쉴 때(베이스·조정)엔
    거래량 적어야(dry-up) 좋다. 즉 같은 '거래량 적음'도 국면에 따라 정반대.
    """
    v = r.vol_vs_avg
    a, d = r.accum_days or 0, r.distrib_days or 0
    close, ma50, ma200 = r.close, r.ma50, r.ma200
    pivot = r.vcp_pivot or r.pivot_price

    GOOD = ("good", {"light": "#15803d", "dark": "#4ade80"}, "🟢")
    WATCH = ("watch", {"light": "#b45309", "dark": "#fbbf24"}, "🟡")
    BAD = ("bad", {"light": "#b91c1c", "dark": "#f87171"}, "🔴")
    NEU = ("neutral", {"light": "#52514e", "dark": "#8b96a8"}, "⚪")

    def mk(t, headline, detail):
        return {"status": t[0], "color": t[1], "icon": t[2], "headline": headline, "detail": detail}

    if v is None:
        return mk(NEU, "거래량 자료 부족", "거래량 데이터가 아직 충분하지 않습니다.")

    # 현재 국면 판정
    if r.signal == "STRONG_BUY":
        phase = "breakout"
    elif (ma200 and close and close < ma200) or (ma50 and close and close < ma50):
        phase = "downtrend"
    elif r.vcp_detected or (pivot and close and close < pivot):
        phase = "base"
    else:
        phase = "uptrend"

    if phase == "breakout":
        if v >= 1.4:
            return mk(GOOD, "돌파 거래량 확인 — 좋음",
                      f"피벗 돌파에 대량거래({v:.1f}x) 동반 — 신뢰도 높은 돌파.")
        if v < 1.0:
            return mk(WATCH, "거래량 없는 돌파 — 주의",
                      f"돌파했지만 거래량이 평균 이하({v:.1f}x) — 가짜 돌파 가능성, 거래 확대 확인.")
        return mk(WATCH, "돌파 거래량 보통",
                  f"거래량 {v:.1f}x — 평균 +40% 이상으로 늘면 신뢰도↑.")

    if phase == "base":
        if r.vcp_volume_dryup or v < 0.85:
            return mk(GOOD, "거래량 마름 — 돌파 준비(좋음)",
                      f"베이스에서 거래량 마름({v:.1f}x) — 매물 고갈, 미너비니가 원하는 VCP 상태.")
        if v >= 1.4 and d >= a:
            return mk(WATCH, "베이스 대량 분산 — 주의",
                      f"쉬는 구간에 대량거래({v:.1f}x)+분산일 다수 — 매물 출회 주의.")
        return mk(NEU, "베이스 형성 중", "거래량이 마르는지(dry-up) 지켜보세요.")

    if phase == "downtrend":
        if d >= a + 2 or d >= 5:
            return mk(BAD, "하락 + 분산 — 경계",
                      f"기관 매도 흔적(분산 {d} vs 매집 {a}) — 신규 매수 금물, 보유 시 방어.")
        if v < 0.85:
            return mk(WATCH, "하락하나 거래 한산", "투매는 아니나 추세 약함 — 관망.")
        return mk(WATCH, "추세 약화 구간", "50일선 아래 — 매집/분산 방향 주시.")

    # uptrend
    if a >= d + 2:
        return mk(GOOD, "상승 + 매집 우세 — 건강",
                  f"매집({a})이 분산({d})보다 우세 — 기관 매수 중.")
    if d >= a + 2 or d >= 5:
        return mk(BAD, "상승하나 분산 누적 — 주의",
                  f"대량 하락일(분산 {d}) 누적 — 기관 이탈 가능성.")
    return mk(NEU, "상승 추세 · 균형", "매집·분산이 팽팽 — 돌파 시 거래량 확대 확인.")


def est_revision(up, down, chg) -> dict | None:
    """애널리스트 EPS 추정치 상향/하향 해석. 데이터 없으면(한국 등) None."""
    if up is None and down is None and chg is None:
        return None
    u, d = up or 0, down or 0
    net = u - d
    parts = []
    if u or d:
        parts.append(f"30일 {u}명↑ / {d}명↓")
    if chg is not None:
        parts.append(f"연간EPS 추정 {'+' if chg >= 0 else ''}{chg}%")
    note = " · ".join(parts) if parts else "데이터 부족"
    if net >= 3 or (chg is not None and chg >= 3 and net >= 0):
        return {"label": "추정치 상향", "icon": "📈", "color": {"light": "#15803d", "dark": "#4ade80"}, "note": note, "good": True}
    if net <= -3 or (chg is not None and chg <= -3):
        return {"label": "추정치 하향", "icon": "📉", "color": {"light": "#b91c1c", "dark": "#f87171"}, "note": note, "good": False}
    return {"label": "추정치 보합", "icon": "➖", "color": {"light": "#52514e", "dark": "#8b96a8"}, "note": note, "good": None}


templates.env.globals["fmt_price"] = fmt_price
templates.env.globals["fmt_amount"] = fmt_amount
templates.env.globals["tv_url"] = tv_url
templates.env.globals["fmt_turnover"] = fmt_turnover
templates.env.globals["ad_interpret"] = ad_interpret
templates.env.globals["volume_verdict"] = volume_verdict
templates.env.globals["est_revision"] = est_revision
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


def _fetch_sparklines(db: Session, stock_ids: list[int], days: int = 30) -> dict:
    """최근 N거래일 종가로 미니 스파크라인 SVG points 생성 (표 행용, 배치 조회).

    {stock_id: {"points": "x,y x,y ...", "up": bool}} — 60x20 뷰박스 기준.
    """
    if not stock_ids:
        return {}
    cutoff = date.today() - timedelta(days=days * 2 + 15)  # 주말/휴장 여유
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.date, DailyPrice.close)
        .filter(DailyPrice.stock_id.in_(stock_ids), DailyPrice.date >= cutoff)
        .order_by(DailyPrice.stock_id, DailyPrice.date)
        .all()
    )
    by_stock: dict = {}
    for sid, _, close in rows:
        by_stock.setdefault(sid, []).append(close)

    W, H = 60, 20
    out = {}
    for sid, closes in by_stock.items():
        closes = closes[-days:]
        if len(closes) < 2:
            continue
        lo, hi = min(closes), max(closes)
        span = (hi - lo) or 1
        n = len(closes)
        pts = [
            f"{round(i / (n - 1) * W, 1)},{round(H - ((c - lo) / span) * H, 1)}"
            for i, c in enumerate(closes)
        ]
        out[sid] = {"points": " ".join(pts), "up": closes[-1] >= closes[0]}
    return out


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

    # 표에 쓸 미니 스파크라인(최근 30거래일) — 배치 조회 1회
    sparkline_ids = [s.id for _, s in buy_list] + [s.id for _, s in sell_list]
    sparklines = _fetch_sparklines(db, sparkline_ids)

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
            "sparklines": sparklines,
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


@router.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request, ok: str = "", err: str = ""):
    """이메일 알림 구독 페이지."""
    return templates.TemplateResponse(request, "alerts.html", context={
        "avail_markets": MARKETS, "ok": ok, "err": err,
        "email_enabled": alerts_mod.email_enabled(),
    })


@router.post("/alerts/subscribe")
def alerts_subscribe(email: str = Form(...), market: str = Form("US")):
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        return RedirectResponse("/alerts?err=이메일 형식을 확인하세요", status_code=303)
    if not alerts_mod.email_enabled():
        return RedirectResponse("/alerts?err=관리자가 발송 계정을 아직 설정하지 않았습니다", status_code=303)
    token, already = alerts_mod.add_subscriber(email, market if market in MARKETS else "US")
    if already:
        return RedirectResponse("/alerts?ok=이미 구독 중입니다", status_code=303)
    sent, msg = alerts_mod.send_confirmation(email, token)
    if not sent:
        return RedirectResponse(f"/alerts?err=확인메일 발송 실패 ({msg[:50]})", status_code=303)
    return RedirectResponse("/alerts?ok=확인 메일을 보냈습니다 — 메일함에서 '구독 확정'을 눌러주세요", status_code=303)


@router.get("/alerts/confirm", response_class=HTMLResponse)
def alerts_confirm(request: Request, token: str = ""):
    email = alerts_mod.confirm(token)
    msg = (f"✅ {email} 구독이 확정되었습니다! 매일 새 돌파·매도신호를 보내드립니다."
           if email else "잘못되었거나 만료된 링크입니다.")
    return templates.TemplateResponse(request, "alerts_result.html", context={"msg": msg, "ok": bool(email)})


@router.get("/alerts/unsubscribe", response_class=HTMLResponse)
def alerts_unsubscribe(request: Request, token: str = ""):
    email = alerts_mod.unsubscribe(token)
    msg = f"{email} 구독이 취소되었습니다." if email else "잘못된 링크입니다."
    return templates.TemplateResponse(request, "alerts_result.html", context={"msg": msg, "ok": bool(email)})


@router.get("/vcp", response_class=HTMLResponse)
def vcp_page(request: Request, db: Session = Depends(get_db)):
    """VCP 레지스트리 — 형성 중(감시 대상) + 최근 돌파(셋업 결과) 이력."""
    from app.models import VCPEvent

    latest = _latest_screen_date(db) or date.today()

    def _days(a, b):
        return (a - b).days if (a and b) else None

    # 형성 중 — RS 강한 순
    forming = (
        db.query(VCPEvent, Stock)
        .join(Stock, Stock.id == VCPEvent.stock_id)
        .filter(VCPEvent.status == "forming")
        .order_by(VCPEvent.rs_rank.desc())
        .all()
    )
    forming_rows = []
    for ev, s in forming:
        gap = (round((ev.pivot_price / ev.close - 1) * 100, 1)
               if (ev.pivot_price and ev.close and ev.close < ev.pivot_price) else None)
        forming_rows.append({"ev": ev, "stock": s, "market": s.market or "US",
                             "days": _days(latest, ev.first_detected), "gap": gap})

    # 최근 30일 돌파 — 돌파 후 성과(현재가 대비)까지
    broke = (
        db.query(VCPEvent, Stock)
        .join(Stock, Stock.id == VCPEvent.stock_id)
        .filter(VCPEvent.status == "broke_out", VCPEvent.breakout_date >= latest - timedelta(days=30))
        .order_by(VCPEvent.breakout_date.desc())
        .all()
    )
    broke_rows = []
    for ev, s in broke:
        cur = (db.query(ScreeningResult.close)
               .filter(ScreeningResult.stock_id == s.id)
               .order_by(ScreeningResult.screen_date.desc()).first())
        cur_close = cur[0] if cur else None
        ret = (round((cur_close / ev.breakout_price - 1) * 100, 1)
               if (cur_close and ev.breakout_price) else None)
        broke_rows.append({"ev": ev, "stock": s, "market": s.market or "US",
                           "base_days": _days(ev.breakout_date, ev.first_detected),
                           "cur_close": cur_close, "ret": ret})

    return templates.TemplateResponse(request, "vcp.html", context={
        "forming": forming_rows, "broke": broke_rows, "screen_date": latest,
    })


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
            "signal_color": SIGNAL_COLORS.get(r.signal, SIGNAL_COLOR_DEFAULT) if (r and r.signal) else SIGNAL_COLOR_DEFAULT,
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
                     "color": "rgba(22,163,74,0.35)" if up else "rgba(220,38,38,0.35)"})
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
