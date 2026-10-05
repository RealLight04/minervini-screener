import logging
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Request, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DailyPrice, Fundamental, ScreeningResult, Stock
from app.screener import (
    SIGNAL_LABELS, build_trade_plan, compute_market_breadth,
    _ACCOUNT_RISK_PCT, _EXAMPLE_ACCOUNT, _EXAMPLE_ACCOUNT_KR, _MAX_WEIGHT_PCT, _PROFIT_R_MULTIPLE,
)
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")

# 템플릿에서 신호 한국어 라벨/색상 사용
SIGNAL_COLORS = {
    "STRONG_BUY": {"light": "#0c8055", "dark": "#f2c200"},
    "BUY": {"light": "#1a7d5c", "dark": "#52c47a"},
    "WATCH": {"light": "#78716c", "dark": "#a2aab1"},
    "SELL": {"light": "#8a5c00", "dark": "#e5483d"},
    "AVOID": {"light": "#a39c92", "dark": "#808990"},
}
SIGNAL_COLOR_DEFAULT = {"light": "#78716c", "dark": "#a2aab1"}
templates.env.globals["signal_labels"] = SIGNAL_LABELS
templates.env.globals["signal_colors"] = SIGNAL_COLORS
templates.env.globals["signal_color_default"] = SIGNAL_COLOR_DEFAULT

MARKETS = ["US", "KOSPI", "KOSDAQ"]
MARKET_LABELS = {"US": "미국 (S&P 500)", "KOSPI": "코스피", "KOSDAQ": "코스닥"}


# ───── PWA(설치형 앱) 루트 자원 ─────
# 서비스워커는 루트 경로(/sw.js)에서 제공해야 스코프가 사이트 전체(/)가 된다.
@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@router.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    return FileResponse("static/manifest.webmanifest", media_type="application/manifest+json")


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


def heat_bg(pct: float, positive: bool = True) -> str:
    """0~100 강도 → 초록/빨강 계열 배경색(color-mix). 라이트/다크 모드에 자동 대응(--green/--red/--surface 재사용)."""
    pct = max(0.0, min(100.0, pct))
    color_var = "var(--green)" if positive else "var(--red)"
    alpha = 6 + pct * 0.30
    return f"color-mix(in srgb, {color_var} {alpha:.0f}%, var(--surface))"


def rs_heat(rs) -> str | None:
    """RS 순위(0~99, 클수록 강세) → 히트맵 배경색. 50 이하는 무색, 99에서 최대 강도."""
    if rs is None:
        return None
    pct = max(0.0, rs - 50) / 49 * 100
    return heat_bg(pct, positive=True)


# 회사 로고 API 없이(신규 외부 의존성/ToS 리스크 회피) 티커별로 고정된 이니셜 아바타 색상 부여.
_AVATAR_PALETTE = [
    "#5b7a9e", "#7a8a5b", "#9e6b5b", "#6b5b9e",
    "#5b9e8f", "#9e5b7a", "#8a7a5b", "#5b7a7a",
]


def ticker_avatar(ticker: str) -> dict:
    """티커 → 이니셜 아바타(이니셜 1~2자 + 결정론적 배경색). 실제 로고 없이 가벼운 행 식별자."""
    base = ticker.split(".")[0]  # 한국 티커의 .KS/.KQ 접미사 제거
    initials = base[:2].upper() if len(base) >= 2 else base.upper()
    idx = sum(ord(c) for c in base) % len(_AVATAR_PALETTE)
    return {"initials": initials, "color": _AVATAR_PALETTE[idx]}


def ret_heat(ret) -> str | None:
    """돌파 후 수익률(%) → 히트맵 배경색. +25%/-15%에서 최대 강도."""
    if ret is None:
        return None
    if ret >= 0:
        return heat_bg(min(100.0, ret / 25 * 100), positive=True)
    return heat_bg(min(100.0, abs(ret) / 15 * 100), positive=False)


def growth_heat(value, good) -> str | None:
    """분기 성장률(%) → good 임계값 대비 강도의 히트맵 배경색(임계값 도달 시 최대 강도)."""
    if value is None or not good:
        return None
    if value >= 0:
        return heat_bg(min(100.0, value / good * 100), positive=True)
    return heat_bg(min(100.0, abs(value) / good * 100), positive=False)


def vol_heat(v) -> str | None:
    """거래량 배수(50일 평균 대비) → '많음' 구간(1.4x~) 강도 히트맵. 1.4x=옅게, 3.0x+=진하게."""
    if v is None or v < 1.4:
        return None
    pct = min(100.0, (v - 1.4) / 1.6 * 100)
    return heat_bg(pct, positive=True)


def ad_interpret(accum, distrib) -> dict:
    """최근 25일 매집(accum)/분산(distrib) 일수 → 해석. 미너비니: 대량거래일의 방향이 기관 의중.

    매집일 = 대량거래 + 상승 마감(기관이 사들인 흔적)
    분산일 = 대량거래 + 하락 마감(기관이 판 흔적)
    """
    GRAY = {"light": "#78716c", "dark": "#a2aab1"}
    GREEN = {"light": "#0a7048", "dark": "#52c47a"}
    RED = {"light": "#c81e0a", "dark": "#ef5a4f"}
    a, d = accum or 0, distrib or 0
    if a == 0 and d == 0:
        return {"verdict": "자료 부족", "color": GRAY, "accum": a, "distrib": d,
                "note": "대량거래일 뚜렷하지 않음."}
    if d >= 5 and d >= a:
        return {"verdict": "분산 우세", "color": RED, "accum": a, "distrib": d,
                "note": "대량 하락일 많음, 기관 매도 흔적. 신규 매수 신중, 보유 시 경계."}
    if a >= d + 2:
        return {"verdict": "매집 우세", "color": GREEN, "accum": a, "distrib": d,
                "note": "대량 상승일 우세 → 기관 매수 흔적(건강), 미너비니 선호 패턴."}
    if d >= a + 2:
        return {"verdict": "분산 우세", "color": RED, "accum": a, "distrib": d,
                "note": "대량 하락일 우세 → 기관 매도 압력, 추세 약화 주의."}
    return {"verdict": "중립", "color": GRAY, "accum": a, "distrib": d,
            "note": "매집·분산 팽팽. 대량거래 동반 돌파 대기."}


def ud_interpret(ratio) -> dict | None:
    """U/D 거래량 비율(50일 상승일 거래량합÷하락일 거래량합) 해석.

    IBD·미너비니가 쓰는 표준 매집/분산 지표. 단순 일수 카운트와 달리 거래 '규모'까지
    담아, >1이면 상승에 힘이 실린(매집) 상태, <1이면 하락에 물량이 나온(분산) 상태.
    """
    if ratio is None:
        return None
    GRAY = {"light": "#78716c", "dark": "#a2aab1"}
    GREEN = {"light": "#0a7048", "dark": "#52c47a"}
    RED = {"light": "#c81e0a", "dark": "#ef5a4f"}
    if ratio >= 1.5:
        return {"verdict": "강한 매집", "color": GREEN, "ratio": ratio,
                "note": "상승일 거래량이 하락일의 1.5배 이상 → 기관이 적극적으로 사들이는 중(강력)."}
    if ratio >= 1.0:
        return {"verdict": "매집 우위", "color": GREEN, "ratio": ratio,
                "note": "상승일 거래량이 더 많음 → 수급이 매수 쪽(건강)."}
    if ratio >= 0.8:
        return {"verdict": "중립", "color": GRAY, "ratio": ratio,
                "note": "상승·하락 거래량이 비슷 → 방향성이 아직 약합니다."}
    return {"verdict": "분산 우위", "color": RED, "ratio": ratio,
            "note": "하락일 거래량이 더 많음 → 매물 출회(기관 매도 흔적), 신규 매수 신중."}


def dryup_note(ratio) -> str:
    """dry-up 비율(최근10일/50일 평균)을 말로. 낮을수록 거래량이 많이 말랐다는 뜻(성과 예측은 아님)."""
    if ratio is None:
        return ""
    if ratio <= 0.6:
        return f"거래량 크게 마름({ratio:.2f}x)"
    if ratio <= 0.85:
        return f"거래량이 마르는 중({ratio:.2f}x)"
    if ratio <= 1.1:
        return f"거래량 보통({ratio:.2f}x)"
    return f"거래량이 늘고 있음({ratio:.2f}x)"


def volume_verdict(r) -> dict:
    """종목의 '현재 국면 + 거래량'을 조합해 거래량이 지금 건강한지 한 줄로 판정.

    같은 '거래량 적음'도 국면에 따라 뜻이 다르다(베이스에선 마름, 돌파에선 약한 돌파). 다만 돌파일 거래량과
    베이스 마름은 2010~2026 백테스트에서 성과 차이를 못 만들었으므로, 그 둘은 색 판정 없이 사실만 적는다.
    매집·분산(U/D)과 하락 구간 경고는 이 검증과 별개라 그대로 둔다.
    """
    v = r.vol_vs_avg
    a, d = r.accum_days or 0, r.distrib_days or 0
    close, ma50, ma200 = r.close, r.ma50, r.ma200
    pivot = r.vcp_pivot or r.pivot_price

    GOOD = ("good", {"light": "#0a7048", "dark": "#52c47a"}, "🟢")
    WATCH = ("watch", {"light": "#8a5c00", "dark": "#f2c200"}, "🟡")
    BAD = ("bad", {"light": "#c81e0a", "dark": "#ef5a4f"}, "🔴")
    NEU = ("neutral", {"light": "#78716c", "dark": "#a2aab1"}, "⚪")

    def mk(t, headline, detail):
        return {"status": t[0], "color": t[1], "icon": t[2], "headline": headline, "detail": detail}

    if v is None:
        return mk(NEU, "거래량 자료 부족", "거래량 데이터 아직 부족.")

    # 현재 국면 판정
    if r.signal == "STRONG_BUY":
        phase = "breakout"
    elif (ma200 and close and close < ma200) or (ma50 and close and close < ma50):
        phase = "downtrend"
    elif r.vcp_detected or (pivot and close and close < pivot):
        phase = "base"
    else:
        phase = "uptrend"

    ud = r.ud_volume_ratio
    dry = r.dryup_ratio

    if phase == "breakout":
        # 2010~2026 백테스트(트렌드 템플릿 통과 종목, 5천여 건): 돌파일 거래량 크기도, 베이스 거래량 마름도
        # 성과 차이를 만들지 못했다. 그래서 건강·위험 같은 색 판정은 하지 않고 사실만 적는다(모두 중립).
        if v >= 2.0:
            return mk(NEU, "대량 거래 돌파",
                      f"거래량 {v:.1f}x. 백테스트에서 돌파일 거래량 크기와 성과 사이에 뚜렷한 관계는 없었음.")
        if r.vcp_volume_dryup or (dry is not None and dry < 0.85):
            return mk(NEU, "마른 베이스에서 돌파",
                      f"베이스 거래량이 마른 상태에서 피벗 돌파(당일 {v:.1f}x). 거래량 마름도 백테스트 성과 차이는 없었음.")
        return mk(NEU, "피벗 돌파",
                  f"당일 거래량 {v:.1f}x. 돌파일 거래량도 베이스 거래량 마름도 백테스트 성과와 뚜렷한 관계는 없었음.")

    if phase == "base":
        if r.vcp_volume_dryup or (dry is not None and dry < 0.85) or v < 0.85:
            grade = f"{dry:.2f}x" if dry is not None else f"{v:.1f}x"
            return mk(NEU, "베이스 거래량 마름",
                      f"베이스에서 거래량이 마름({grade}). VCP 모양 조건에는 맞지만 이후 성과를 예측하지는 않음.")
        if ud is not None and ud < 0.8:
            return mk(WATCH, "베이스 대량 분산 → 주의",
                      f"쉬는 구간에 하락 거래량 우세(U/D {ud}) → 매물 출회 주의.")
        if v >= 1.4 and d >= a:
            return mk(WATCH, "베이스 대량 분산 → 주의",
                      f"쉬는 구간에 대량거래({v:.1f}x)+분산일 다수 → 매물 출회 주의.")
        return mk(NEU, "베이스 형성 중", "거래량이 마르는지(dry-up) 관찰.")

    if phase == "downtrend":
        if d >= a + 2 or d >= 5:
            return mk(BAD, "하락 + 분산 → 경계",
                      f"기관 매도 흔적(분산 {d} vs 매집 {a}) → 신규 매수 금물, 보유 시 방어.")
        if v < 0.85:
            return mk(WATCH, "하락하나 거래 한산", "투매는 아니나 추세 약함 → 관망.")
        return mk(WATCH, "추세 약화 구간", "50일선 아래 → 매집/분산 방향 주시.")

    # uptrend — U/D 비율을 우선 사용(규모까지 반영), 없으면 매집/분산 일수로 폴백
    if ud is not None:
        if ud >= 1.25:
            return mk(GOOD, "상승 + 매집 우세 → 건강",
                      f"U/D 거래량 {ud} → 상승에 힘이 실림(기관 매수).")
        if ud < 0.8:
            return mk(BAD, "상승하나 분산 → 주의",
                      f"U/D 거래량 {ud} → 하락에 물량 출회, 추세 약화 가능.")
        return mk(NEU, "상승 추세 · 균형",
                  f"U/D 거래량 {ud} → 방향성 뚜렷하지 않음, 돌파 시 거래량 확대 확인.")
    if a >= d + 2:
        return mk(GOOD, "상승 + 매집 우세 → 건강",
                  f"매집({a})이 분산({d})보다 우세 → 기관 매수 중.")
    if d >= a + 2 or d >= 5:
        return mk(BAD, "상승하나 분산 누적 → 주의",
                  f"대량 하락일(분산 {d}) 누적 → 기관 이탈 가능성.")
    return mk(NEU, "상승 추세 · 균형", "매집·분산이 팽팽 → 돌파 시 거래량 확대 확인.")


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
        return {"label": "추정치 상향", "icon": "📈", "color": {"light": "#0a7048", "dark": "#52c47a"}, "note": note, "good": True}
    if net <= -3 or (chg is not None and chg <= -3):
        return {"label": "추정치 하향", "icon": "📉", "color": {"light": "#c81e0a", "dark": "#ef5a4f"}, "note": note, "good": False}
    return {"label": "추정치 보합", "icon": "➖", "color": {"light": "#78716c", "dark": "#a2aab1"}, "note": note, "good": None}


templates.env.globals["fmt_price"] = fmt_price
templates.env.globals["fmt_amount"] = fmt_amount
templates.env.globals["tv_url"] = tv_url
templates.env.globals["fmt_turnover"] = fmt_turnover
templates.env.globals["ad_interpret"] = ad_interpret
templates.env.globals["ud_interpret"] = ud_interpret
templates.env.globals["dryup_note"] = dryup_note
templates.env.globals["rs_heat"] = rs_heat
templates.env.globals["ret_heat"] = ret_heat
templates.env.globals["growth_heat"] = growth_heat
templates.env.globals["vol_heat"] = vol_heat
templates.env.globals["ticker_avatar"] = ticker_avatar
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


def _expected_last_session(market: str) -> date:
    """지금 시각에 이미 종가가 수집됐어야 할 가장 최근 평일(휴장일은 모름)."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    if market == "US":
        now, cutoff = datetime.now(ZoneInfo("America/New_York")), time(17, 0)
    else:
        now, cutoff = datetime.now(ZoneInfo("Asia/Seoul")), time(16, 0)
    d = now.date() if now.time() >= cutoff else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _price_freshness(db: Session, screen_date: date, market: str) -> dict:
    """화면 결과가 쓴 실제 종가 날짜와, 예상 최신 거래일보다 몇 평일 뒤처졌는지."""
    from sqlalchemy import func
    price_date = (
        db.query(func.max(ScreeningResult.price_date))
        .join(Stock, ScreeningResult.stock_id == Stock.id)
        .filter(ScreeningResult.screen_date == screen_date, Stock.market == market)
        .scalar()
    )
    if price_date is None:
        return {"price_date": None, "lag": 0, "stale": False}
    expected, lag, d = _expected_last_session(market), 0, price_date
    while d < expected:
        d += timedelta(days=1)
        if d.weekday() < 5:
            lag += 1
    return {"price_date": price_date, "lag": lag, "stale": lag >= settings.STALE_WARN_SESSIONS}


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


def _range10(db: Session, stock_ids: list[int]) -> dict:
    """직전 10거래일 변동폭(%) = (최고 고가 - 최저 저가) / 최고 고가. 작을수록 최근 흐름이 조용하다."""
    if not stock_ids:
        return {}
    cutoff = date.today() - timedelta(days=40)
    rows = (
        db.query(DailyPrice.stock_id, DailyPrice.date, DailyPrice.high, DailyPrice.low)
        .filter(DailyPrice.stock_id.in_(stock_ids), DailyPrice.date >= cutoff)
        .order_by(DailyPrice.stock_id, DailyPrice.date)
        .all()
    )
    by: dict = {}
    for sid, _, hi, lo in rows:
        if hi and lo:
            by.setdefault(sid, []).append((hi, lo))
    out = {}
    for sid, hl in by.items():
        last = hl[-10:]
        if len(last) >= 5:
            hi = max(h for h, _ in last)
            out[sid] = round((hi - min(l for _, l in last)) / hi * 100, 1)
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

    # 적극 매수: 피벗을 넘어 5% 이내인 신호 → 별도 카테고리로 최상단 표시
    strong_buy_list = _by_signals(["STRONG_BUY"])
    # 매수 후보: 나머지 매수 신호(BUY). 적극 매수는 위에서 따로 보여줌
    buy_list = _by_signals(["BUY"])

    # 돌파 대기: 피벗 아래에서 코일링 중(매수가 확정) → 돌파 임박 순(피벗까지 가까운 순) 정렬
    breakout_watch = []
    for r, s in buy_list:
        if r.pivot_price and r.close and r.close < r.pivot_price:
            gap = round((r.pivot_price / r.close - 1) * 100, 1)
            breakout_watch.append((r, s, gap))
    breakout_watch.sort(key=lambda x: x[2])
    watch_ids = {s.id for _, s, _ in breakout_watch}
    # 눌림목 대기: 매수 신호 중 돌파 대기가 아닌 것(피벗 위로 연장·베이스 없이 고점 부근)
    extended_list = [(r, s) for r, s in buy_list if s.id not in watch_ids]

    # 스트립을 당기면 펼쳐지는 비행 계획(진입·손절·목표·비중)
    plans = {s.id: build_trade_plan(r, market) for r, s in strong_buy_list + buy_list}

    # 주도주 후보: 매수 신호 3갈래를 한 목록으로 합치고, 직접 판단에 쓰는 수치를 열로 노출.
    # 상태는 매수 신호가 아니라 피벗 대비 위치 태그다(돌파권 / 돌파 대기 / 연장).
    cand_rows = strong_buy_list + buy_list
    range10 = _range10(db, [s.id for _, s in cand_rows])
    candidates = []
    for r, s in cand_rows:
        piv, cl = r.pivot_price, r.close
        gap = round((cl / piv - 1) * 100, 1) if (piv and cl) else None   # +는 피벗 위, -는 피벗까지 남은 거리
        if r.signal == "STRONG_BUY":
            state = "break"
        elif piv and cl and cl < piv:
            state = "wait"
        else:
            state = "ext"
        candidates.append({
            "r": r, "s": s, "state": state, "gap": gap,
            "range10": range10.get(s.id), "volx": r.vol_vs_avg,
        })
    cand_counts = {k: sum(1 for c in candidates if c["state"] == k) for k in ("break", "wait", "ext")}
    # VCP 모양 점수(app/vcp.py) 후보 기준 이상인 수 — 홈의 '모양 N점 이상' 토글에 쓴다
    shape_min = int(settings.VCP_MIN_SCORE)
    cand_counts["shape"] = sum(1 for c in candidates if (c["r"].vcp2_score or 0) >= shape_min)

    # 매도 경고: Stage 2 유지 중 50일선 이탈 종목 (RS 강한 순 상위 30개만 표시)
    sell_all = _by_signals(["SELL"])
    sell_list = sell_all[:30]
    data_list = _by_signals(["DATA"])
    sparklines = {}

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
            "strong_buy_list": strong_buy_list,
            "buy_list": buy_list,
            "breakout_watch": breakout_watch,
            "extended_list": extended_list,
            "plans": plans,
            "candidates": candidates,
            "cand_counts": cand_counts,
            "shape_min": shape_min,
            "data_list": data_list,
            "themes": themes,
            "sell_list": sell_list,
            "sparklines": sparklines,
            "screen_date": screen_date,
            "freshness": _price_freshness(db, screen_date, market),
            "strong_buy_count": len(strong_buy_list),
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


@router.get("/api/search-tickers")
def search_tickers(q: str = "", db: Session = Depends(get_db)):
    """커맨드 팔레트(⌘K)용 실시간 티커 검색 — 티커/회사명 부분일치, 최대 20개."""
    q = q.strip()
    if not q:
        return {"results": []}
    like = f"%{q}%"
    stocks = (
        db.query(Stock)
        .filter((Stock.ticker.ilike(like)) | (Stock.name.ilike(like)))
        .order_by(Stock.ticker)
        .limit(20)
        .all()
    )
    return {"results": [
        {"ticker": s.ticker, "name": s.name or "", "market": s.market}
        for s in stocks
    ]}


VCP2_PART_LABELS = {"trend": "추세", "uptrend": "상승 구조", "contraction": "수축", "volume": "거래량",
                    "higher_low": "저점 상승", "tightness": "타이트함", "proximity": "피벗 근접", "rs": "상대강도"}
VCP2_STATE_LABELS = {"WATCH": "관찰", "NEAR_PIVOT": "피벗 근접", "BREAKOUT": "돌파",
                     "FAILED_BREAKOUT": "돌파 실패", "INVALIDATED": "무효"}


def vcp2_card(r) -> dict | None:
    """VCP 점수 모델 결과를 화면용으로 정리. 점수는 '교과서적 모양에 얼마나 가까운가'이지 수익 예측이 아니다."""
    import json
    from bisect import bisect_right
    from app import vcp as vcp_model

    if r is None or r.vcp2_score is None:
        return None
    try:
        parts = json.loads(r.vcp2_parts or "{}")
    except ValueError:
        parts = {}
    rows = [{"key": k, "label": VCP2_PART_LABELS[k], "points": parts.get(k), "max": mx}
            for k, mx in vcp_model.WEIGHTS.items()]
    qlabel = None
    if r.vcp2_bvr is not None:
        qlabel = vcp_model.QUALITY_LABELS[bisect_right(vcp_model.QUALITY_EDGES, r.vcp2_bvr)]
    return {
        "score": r.vcp2_score, "grade": r.vcp2_grade, "state": r.vcp2_state,
        "state_label": VCP2_STATE_LABELS.get(r.vcp2_state, r.vcp2_state),
        "depths": r.vcp2_depths, "pivot": r.vcp2_pivot, "key": r.vcp2_key,
        "parts": rows, "raw_max": vcp_model.MAX_RAW, "bvr": r.vcp2_bvr, "bvr_label": qlabel, "age": r.vcp2_age,
        "candidate": r.vcp2_score >= vcp_model.MIN_SCORE and r.vcp2_state != "INVALIDATED",
    }


@router.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_detail(ticker: str, request: Request, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(Stock.ticker == ticker.upper()).first()
    if not stock:
        return HTMLResponse("<h2>종목을 찾을 수 없음</h2>", status_code=404)

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
    # Code 33(미너비니): EPS·매출·마진이 함께 3분기 연속 가속. 마진은 분기 영업이익률로 대신한다.
    code33 = accel["eps"] and accel["revenue"] and accel["margin"]

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

    # 내 진입가 계산기 입력. 계산은 브라우저에서 한다(서버 호출 없음).
    calc = None
    if latest_result and latest_result.close:
        is_us = (stock.market or "US") == "US"
        pv, cl = latest_result.pivot_price, latest_result.close
        calc = {
            "market": "US" if is_us else "KR",
            "pivot": pv,
            "close": cl,
            "base_low": latest_result.vcp_base_low,
            "entry": pv if (pv and cl < pv) else cl,
            "account": _EXAMPLE_ACCOUNT if is_us else _EXAMPLE_ACCOUNT_KR,
            "risk_pct": _ACCOUNT_RISK_PCT,
            "max_weight": _MAX_WEIGHT_PCT,
            "stop_pct": settings.STOP_LOSS_PCT,
            "r_mult": _PROFIT_R_MULTIPLE,
            "low_buffer_pct": 1.0,   # 저점 손절은 저점 아래 이만큼 여유(꼬리 한 번에 털리는 걸 줄임). 검증하지 않은 초기값
        }

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
            "calc": calc,
            "vcp2": vcp2_card(latest_result),
        },
    )


@router.get("/api/screen-now")
def trigger_screen(db: Session = Depends(get_db), x_admin_token: str = Header(default="")):
    """수동 스크리닝 트리거 (관리자 전용) — ADMIN_TRIGGER_TOKEN 헤더가 일치해야 동작.
    미설정/불일치 시 404로 존재 자체를 감춤 (공개 Funnel에서 무단 재계산 방지)."""
    import hmac
    expected = settings.ADMIN_TRIGGER_TOKEN
    if not expected or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=404)
    from app.screener import run_daily_screen
    passed = run_daily_screen(db)
    return {"status": "ok", "passed": passed, "date": str(date.today())}


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    """용어 가이드: 수축(T1·T2)·피벗·돌파가 무엇이고 이 사이트가 어떻게 계산하는지."""
    return templates.TemplateResponse(request, "guide.html", context={})


@router.get("/vcp", response_class=HTMLResponse)
def vcp_page(request: Request, db: Session = Depends(get_db)):
    """VCP 일지 — 형성 중(감시 대상) + 최근 돌파(셋업 결과) 이력. 점수 모델(app/vcp.py)의 vcp_formations 기준.

    예전 판정(vcp_events)은 건드리지 않고 보존하며, 돌파 후 성과 통계만 한 줄 요약으로 함께 보여준다."""
    from app.models import VCPFormation

    latest = _latest_screen_date(db) or date.today()

    def _days(a, b):
        return (a - b).days if (a and b) else None

    # 형성 중: 아직 돌파 전이고 추적 중인 형성 — 모양 점수 높은 순
    forming = (
        db.query(VCPFormation, Stock)
        .join(Stock, Stock.id == VCPFormation.stock_id)
        .filter(VCPFormation.resolved_date.is_(None), VCPFormation.breakout_date.is_(None))
        .order_by(VCPFormation.score_last.desc().nullslast(), VCPFormation.rs_rank.desc())
        .all()
    )
    forming_rows = []
    for ev, s in forming:
        gap = (round((ev.pivot / ev.close - 1) * 100, 1)
               if (ev.pivot and ev.close and ev.close < ev.pivot) else None)
        forming_rows.append({"ev": ev, "stock": s, "market": s.market or "US",
                             "days": _days(latest, ev.first_detected), "gap": gap,
                             "depths": (ev.depths or "").replace("/", " → ")})

    # 최근 30일 돌파 — 돌파 후 성과(현재가 대비)까지. 돌파 뒤 실패했거나 무효가 된 것도 기록은 남겨 함께 보인다
    broke = (
        db.query(VCPFormation, Stock)
        .join(Stock, Stock.id == VCPFormation.stock_id)
        .filter(VCPFormation.breakout_date.isnot(None), VCPFormation.breakout_date >= latest - timedelta(days=30))
        .order_by(VCPFormation.breakout_date.desc())
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
                           "base_days": max(0, _days(ev.breakout_date, ev.first_detected) or 0),
                           "cur_close": cur_close, "ret": ret})

    # 형성중→돌파 시각적 연결: 두 표 모두 같은 스파크라인 언어로 종목의 흐름을 보여줌
    all_stock_ids = [s.id for _, s in forming] + [s.id for _, s in broke]
    sparklines = _fetch_sparklines(db, all_stock_ids)

    return templates.TemplateResponse(request, "vcp.html", context={
        "forming": forming_rows, "broke": broke_rows, "screen_date": latest,
        "sparklines": sparklines, "stats": _vcp_outcome_stats(db, VCPFormation),
        "legacy": _vcp_outcome_stats(db),     # 예전 판정(vcp_events)의 돌파 후 성과 — 비교용 한 줄
        "shape_min": int(settings.VCP_MIN_SCORE),
    })


def _vcp_outcome_stats(db: Session, model=None) -> dict:
    """돌파한 건의 실제 손절·목표 결과 — 표본외 성과 검증용 상시 통계.

    model을 안 주면 예전 레지스트리(VCPEvent, status == broke_out), VCPFormation을 주면 breakout_date가 있는 형성.
    outcome_pct는 app.screener.settle_outcomes가 매일 손절(-8%대)·목표(+2.5R)
    도달 여부로 채운다. '현재가 대비'가 아니라 실제 체결 규칙 기준이라 승률 해석이 다르다.
    """
    from app.models import VCPEvent, VCPFormation

    model = model or VCPEvent
    cond = (VCPEvent.status == "broke_out") if model is VCPEvent else VCPFormation.breakout_date.isnot(None)
    rows = (
        db.query(model.outcome, model.regime_at_breakout, model.outcome_pct)
        .filter(cond)
        .all()
    )
    total = len(rows)
    resolved = [r for r in rows if r.outcome in ("stop", "target")]
    stop_n = sum(1 for r in resolved if r.outcome == "stop")
    target_n = sum(1 for r in resolved if r.outcome == "target")
    pending_n = sum(1 for r in rows if r.outcome is None)
    timeout_n = sum(1 for r in rows if r.outcome == "timeout")

    def avg_pct(outcome):
        vals = [r.outcome_pct for r in resolved if r.outcome == outcome and r.outcome_pct is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    win_rate = round(target_n / len(resolved) * 100) if resolved else None
    avg_stop, avg_target = avg_pct("stop"), avg_pct("target")
    expectancy = None
    if resolved and avg_stop is not None and avg_target is not None:
        expectancy = round((target_n * avg_target + stop_n * avg_stop) / len(resolved), 1)

    by_regime = {}
    for mk in ("BULL", "NEUTRAL", "BEAR"):
        sub = [r for r in rows if r.regime_at_breakout == mk and r.outcome in ("stop", "target")]
        if not sub:
            continue
        t = sum(1 for r in sub if r.outcome == "target")
        by_regime[mk] = {"n": len(sub), "win_rate": round(t / len(sub) * 100)}

    from app.screener import _PROFIT_R_MULTIPLE
    return {
        "stop_pct": settings.STOP_LOSS_PCT,
        "target_pct": round(settings.STOP_LOSS_PCT * _PROFIT_R_MULTIPLE, 1),
        "total": total, "resolved": len(resolved), "pending": pending_n, "timeout": timeout_n,
        "stop_n": stop_n, "target_n": target_n, "win_rate": win_rate,
        "avg_stop": avg_stop, "avg_target": avg_target, "expectancy": expectancy,
        "by_regime": by_regime,
    }


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

    # 매수 지점: build_trade_plan과 동일한 분기(현재가 돌파/피벗 대기/50일선 눌림)로 계산.
    # 손절은 스크리닝이 저장한 값 그대로 — 목록·계획표·차트가 같은 숫자를 보여야 한다
    buy, buy_label = None, None
    chart_stop = latest.stop_loss if latest else None
    if latest and latest.signal in ("BUY", "STRONG_BUY"):
        if latest.signal == "STRONG_BUY" and latest.pivot_price:
            buy, buy_label = round(latest.close, 2), "돌파 후 현재가 진입"
        elif latest.pivot_price and latest.close < latest.pivot_price:
            buy, buy_label = latest.pivot_price, "피벗 돌파 매수"
        elif latest.ma50:
            buy, buy_label = round(latest.ma50, 2), "50일선 눌림 매수"

    # VCP 수축 구간(T1, T2, …): 판정과 같은 지그재그에서 고점→다음 저점 쌍
    from app.screener import vcp_swings, VCP_LOOKBACK
    contractions = []
    if n >= 60:
        zz = vcp_swings(close)
        off = max(0, n - VCP_LOOKBACK)
        for a, b in zip(zz, zz[1:]):
            if a[2] == "H" and b[2] == "L" and a[1] > b[1] > 0:
                contractions.append({
                    "h_time": rows[off + a[0]][0].isoformat(), "h": round(a[1], 2),
                    "l_time": rows[off + b[0]][0].isoformat(), "l": round(b[1], 2),
                    "pct": round((a[1] - b[1]) / a[1] * 100, 1),
                })

    # VCP 점수 모델의 수축 구간(T1, T2, …): 베이스 고점에서 가장 깊은 저점까지가 T1, 그 뒤는 지그재그.
    # 점수는 DB에 저장된 값을 쓰고, 여기선 차트에 그릴 위치만 즉석 계산한다(종목 하나라 가볍다).
    vcp2 = None
    try:
        from app import vcp as vcp_model
        ok = [i for i in range(max(0, n - 400), n) if rows[i][2] and rows[i][3] and rows[i][4]]
        if len(ok) >= 60:
            f2, _ = vcp_model.detect_latest([rows[i][2] for i in ok], [rows[i][3] for i in ok],
                                            [rows[i][4] for i in ok], [rows[i][5] or 0 for i in ok])
            if f2 is not None:
                def tm(k):
                    return rows[ok[k]][0].isoformat()
                vcp2 = {
                    "contractions": [{"h_time": tm(c.peak_idx), "h": round(c.peak_price, 2),
                                      "l_time": tm(c.low_idx), "l": round(c.low_price, 2),
                                      "pct": round(c.depth_pct, 1)} for c in f2.contractions],
                    "pivot": round(f2.pivot, 2), "pivot_time": tm(f2.pivot_idx),
                    "base_high": round(f2.base_high, 2), "base_high_time": tm(f2.base_high_idx),
                }
    except Exception:
        logger.exception("차트용 VCP 수축 계산 실패(%s)", ticker)

    return {
        "ticker": stock.ticker,
        "name": stock.name,
        "market": stock.market or "US",
        "currency": "$" if (stock.market or "US") == "US" else "₩",
        "vcp2": vcp2,
        "candles": candles,
        "volume": vols,
        "ma50": ma50_s,
        "ma150": ma150_s,
        "ma200": ma200_s,
        "pivot": latest.pivot_price if latest else None,
        "stop": chart_stop,
        "buy": buy,
        "buy_label": buy_label,
        "contractions": contractions,
        "vcp_detected": bool(latest.vcp_detected) if latest else False,
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
