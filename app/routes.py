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


templates.env.globals["fmt_price"] = fmt_price
templates.env.globals["fmt_amount"] = fmt_amount
templates.env.globals["market_labels"] = MARKET_LABELS


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

    # 매도 경고: Stage 2 유지 중 50일선 이탈 종목 (RS 강한 순 상위 30개만 표시)
    sell_all = _by_signals(["SELL"])
    sell_list = sell_all[:30]

    # 시장 국면(breadth) — 선택한 시장 기준
    breadth = compute_market_breadth(db, screen_date, market=market)

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "buy_list": buy_list,
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
        .limit(4)
        .all()
    )[::-1]
    y_funds = (
        db.query(Fundamental)
        .filter(Fundamental.stock_id == stock.id, Fundamental.period_type == "Y")
        .order_by(Fundamental.period_date.desc())
        .limit(3)
        .all()
    )[::-1]

    # 분기 EPS 성장률 가속 여부 (최근 분기로 갈수록 YoY 성장률이 커지는가)
    q_growths = [f.eps_growth_yoy for f in q_funds[-3:] if f.eps_growth_yoy is not None]
    eps_accelerating = len(q_growths) >= 2 and all(
        q_growths[i] < q_growths[i + 1] for i in range(len(q_growths) - 1)
    )

    trade_plan = build_trade_plan(latest_result, stock.market or "US") if latest_result else None

    return templates.TemplateResponse(
        request,
        "stock.html",
        context={
            "stock": stock,
            "result": latest_result,
            "history": history,
            "q_funds": q_funds,
            "y_funds": y_funds,
            "eps_accelerating": eps_accelerating,
            "trade_plan": trade_plan,
        },
    )


@router.get("/api/screen-now")
def trigger_screen(db: Session = Depends(get_db)):
    """수동 스크리닝 트리거 (개발/테스트용)"""
    from app.screener import run_daily_screen
    passed = run_daily_screen(db)
    return {"status": "ok", "passed": passed, "date": str(date.today())}


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
