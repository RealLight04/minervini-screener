"""
Minervini Trend Template 스크리닝 핵심 로직

Stage 2 기술적 조건:
  1. 현재가 > 150일 MA
  2. 현재가 > 200일 MA
  3. 150일 MA > 200일 MA
  4. 200일 MA가 1개월 이상 상승 중
  5. 현재가 > 50일 MA
  6. 현재가 >= 52주 저가의 130% (저가 대비 30% 이상)
  7. 현재가 >= 52주 고가의 75% (고가 대비 25% 이내)
  8. RS 랭킹 상위 30% 이내
"""
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models import DailyPrice, Fundamental, ScreeningResult, Stock
from config import settings

logger = logging.getLogger(__name__)


def _get_price_series(db: Session, stock_id: int, days: int = 260) -> pd.Series:
    """DB에서 주가 시계열 가져오기 (날짜 오름차순)"""
    cutoff = date.today() - timedelta(days=days)
    rows = (
        db.query(DailyPrice.date, DailyPrice.close)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.date >= cutoff)
        .order_by(DailyPrice.date)
        .all()
    )
    if not rows:
        return pd.Series(dtype=float)
    idx, vals = zip(*rows)
    return pd.Series(list(vals), index=list(idx))


def _get_volume_series(db: Session, stock_id: int, days: int = 120) -> pd.Series:
    """DB에서 거래량 시계열 가져오기 (날짜 오름차순)"""
    cutoff = date.today() - timedelta(days=days)
    rows = (
        db.query(DailyPrice.date, DailyPrice.volume)
        .filter(DailyPrice.stock_id == stock_id, DailyPrice.date >= cutoff)
        .order_by(DailyPrice.date)
        .all()
    )
    if not rows:
        return pd.Series(dtype=float)
    idx, vals = zip(*rows)
    return pd.Series([float(v) if v is not None else 0.0 for v in vals], index=list(idx))


def _calc_rs_scores(db: Session, screen_date: date, stock_ids: set | None = None) -> dict[int, float]:
    """
    RS(상대강도) 계산: 52주 수익률 기준 백분위 (0~100, 높을수록 강함).
    stock_ids가 주어지면 그 집합(=같은 시장) 안에서만 상대 랭킹 → 시장 간 혼합 방지.
    Minervini는 IBD RS Line과 유사하게 52주 퍼포먼스를 사용.
    """
    cutoff_52w = screen_date - timedelta(weeks=52)
    cutoff_1w = screen_date - timedelta(days=7)

    # 현재 종가
    today_rows = (
        db.query(DailyPrice.stock_id, DailyPrice.close, DailyPrice.date)
        .filter(DailyPrice.date >= cutoff_1w)
        .all()
    )
    # 종목별 가장 최근 종가
    today_close_raw: dict[int, tuple] = {}
    for stock_id, close, d in today_rows:
        if stock_id not in today_close_raw or d > today_close_raw[stock_id][1]:
            today_close_raw[stock_id] = (close, d)
    today_close = {sid: v[0] for sid, v in today_close_raw.items()}

    # 52주 전 종가
    year_ago_rows = (
        db.query(DailyPrice.stock_id, DailyPrice.close, DailyPrice.date)
        .filter(DailyPrice.date >= cutoff_52w - timedelta(days=10), DailyPrice.date <= cutoff_52w + timedelta(days=10))
        .all()
    )
    year_ago_close: dict[int, float] = {}
    for stock_id, close, d in year_ago_rows:
        if stock_id not in year_ago_close:
            year_ago_close[stock_id] = (close, d)
        else:
            # 52주 전 날짜에 가장 가까운 것
            if abs((d - cutoff_52w).days) < abs((year_ago_close[stock_id][1] - cutoff_52w).days):
                year_ago_close[stock_id] = (close, d)
    year_ago_close = {sid: v[0] for sid, v in year_ago_close.items()}

    common = set(today_close.keys()) & set(year_ago_close.keys())
    if stock_ids is not None:
        common &= stock_ids  # 같은 시장 안에서만 상대 랭킹
    if not common:
        return {}

    perf = {
        sid: (today_close[sid] / year_ago_close[sid] - 1) * 100
        for sid in common
        if year_ago_close[sid] > 0
    }
    series = pd.Series(perf)
    # 백분위 랭킹 (0~100, 높을수록 상대강도 강함)
    ranked = series.rank(pct=True) * 100
    return ranked.to_dict()


def screen_stock(db: Session, stock: Stock, screen_date: date, rs_rank: float) -> ScreeningResult:
    """단일 종목 Minervini 스크리닝 실행"""
    series = _get_price_series(db, stock.id, days=370)  # 200일MA + 1개월 여유 확보

    result = ScreeningResult(stock_id=stock.id, screen_date=screen_date)

    if len(series) < 200:
        result.technical_pass = False
        result.fundamental_pass = False
        result.final_pass = False
        return result

    close = series.iloc[-1]
    ma50 = series.rolling(50).mean().iloc[-1]
    ma150 = series.rolling(150).mean().iloc[-1]
    ma200 = series.rolling(200).mean().iloc[-1]
    # 1개월(약 21거래일) 전 200일 MA
    ma200_month_ago = series.rolling(200).mean().iloc[-22] if len(series) >= 222 else None

    week52_high = series.tail(252).max()
    week52_low = series.tail(252).min()

    # 지표 저장
    result.close = round(close, 4)
    result.ma50 = round(ma50, 4)
    result.ma150 = round(ma150, 4)
    result.ma200 = round(ma200, 4)
    result.ma200_month_ago = round(ma200_month_ago, 4) if ma200_month_ago is not None else None
    result.week52_high = round(week52_high, 4)
    result.week52_low = round(week52_low, 4)
    result.rs_rank = round(rs_rank, 2)

    # ─── 기술적 8가지 조건 ───
    result.cond_price_above_ma150 = bool(close > ma150)
    result.cond_price_above_ma200 = bool(close > ma200)
    result.cond_ma150_above_ma200 = bool(ma150 > ma200)
    result.cond_ma200_uptrend = bool(ma200_month_ago is not None and ma200 > ma200_month_ago)
    result.cond_price_above_ma50 = bool(close > ma50)
    result.cond_above_52w_low_30pct = bool(close >= week52_low * 1.30)
    result.cond_within_52w_high_25pct = bool(close >= week52_high * 0.75)
    result.cond_rs_rank = bool(rs_rank >= (100 - settings.RS_TOP_PERCENTILE))

    result.technical_pass = all([
        result.cond_price_above_ma150,
        result.cond_price_above_ma200,
        result.cond_ma150_above_ma200,
        result.cond_ma200_uptrend,
        result.cond_price_above_ma50,
        result.cond_above_52w_low_30pct,
        result.cond_within_52w_high_25pct,
        result.cond_rs_rank,
    ])

    # ─── 펀더멘털 조건 ───
    result.fundamental_pass = _check_fundamentals(db, stock.id, result)

    # ─── VCP 탐지 (피벗 가격 포함) ───
    vcp_result = detect_vcp(series)
    result.vcp_detected = vcp_result["detected"]
    result.vcp_contractions = vcp_result["contractions"]
    result.vcp_pivot = vcp_result.get("pivot")   # 레지스트리용(매수신호 여부 무관)
    result.vcp_base_low = vcp_result.get("base_low")
    result.vcp_base_high = vcp_result.get("base_high")
    cp = vcp_result.get("correction_pcts")
    result.vcp_last_contraction = cp[-1] if cp else None
    # 피벗: VCP가 있으면 그 피벗, 없으면 베이스 피벗으로 폴백 (돌파대기/적극매수 표면화)
    pivot = vcp_result.get("pivot") if result.vcp_detected else None
    if pivot is None:
        pivot = detect_pivot(series)

    # ─── 거래량 / 유동성 분석 ───
    vol_series = _get_volume_series(db, stock.id, days=120)
    if len(vol_series) >= 50:
        avg_vol_50 = float(vol_series.tail(50).mean())
        recent_vol = float(vol_series.iloc[-1])
        recent_10_avg = float(vol_series.tail(10).mean())
        result.avg_volume = round(avg_vol_50, 0)
        result.vol_vs_avg = round(recent_vol / avg_vol_50, 2) if avg_vol_50 else None
        # VCP 거래량 마름: 최근 10일 평균이 50일 평균의 85% 미만 = 매도세 고갈
        result.vcp_volume_dryup = bool(avg_vol_50 and recent_10_avg < avg_vol_50 * 0.85)
        # 매집/분산: 최근 25거래일 중 '상승+대량'=매집(기관매수), '하락+대량'=분산(기관매도)
        accum = distrib = 0
        for d, v in vol_series.tail(25).items():
            if d not in series.index:
                continue
            pos = series.index.get_loc(d)
            if pos == 0 or float(v) <= avg_vol_50:   # 첫날이거나 평균 이하(대량 아님) 제외
                continue
            if float(series.iloc[pos]) > float(series.iloc[pos - 1]):
                accum += 1
            elif float(series.iloc[pos]) < float(series.iloc[pos - 1]):
                distrib += 1
        result.accum_days = accum
        result.distrib_days = distrib

        # U/D 거래량 비율: 최근 50거래일 '상승 마감일 거래량합 ÷ 하락 마감일 거래량합'.
        # 기관 매집(>1.0)·분산(<1.0)의 표준 지표(IBD U/D Ratio). 단순 일수 카운트와 달리
        # 거래 규모까지 반영해, 큰손이 사고 있는지 팔고 있는지를 훨씬 안정적으로 보여준다.
        up_vol = down_vol = 0.0
        for d, v in vol_series.tail(50).items():
            if d not in series.index:
                continue
            pos = series.index.get_loc(d)
            if pos == 0:
                continue
            chg = float(series.iloc[pos]) - float(series.iloc[pos - 1])
            if chg > 0:
                up_vol += float(v)
            elif chg < 0:
                down_vol += float(v)
        if down_vol > 0:
            result.ud_volume_ratio = round(up_vol / down_vol, 2)
        elif up_vol > 0:
            result.ud_volume_ratio = 9.99   # 하락일 거래량 전무 = 극단적 매집
        else:
            result.ud_volume_ratio = None

        # 거래량 마름 정도(등급): 최근 10일 평균 / 50일 평균. 낮을수록 매물 고갈(dry-up).
        result.dryup_ratio = round(recent_10_avg / avg_vol_50, 2) if avg_vol_50 else None

        # 유동성: 최소 주가 & 최소 평균 거래량 충족 (페니/저유동 제외). 최소주가는 시장별.
        min_price = settings.MIN_PRICE if stock.market == "US" else settings.MIN_PRICE_KR
        result.liquidity_pass = bool(close >= min_price and avg_vol_50 >= settings.MIN_VOLUME)
    else:
        result.liquidity_pass = True  # 거래량 데이터 부족 시 배제하지 않음

    result.final_pass = result.technical_pass and result.fundamental_pass and result.liquidity_pass

    # ─── 매수/매도 의견 산출 ───
    signal, reason = compute_signal(result, pivot, stock.market or "US")
    result.signal = signal
    result.signal_reason = reason
    if signal in ("STRONG_BUY", "BUY"):
        if pivot and close <= pivot * 1.05:
            # 유효한 돌파 매수가: 피벗 + 손절 -8% (Minervini 7~8%)
            result.pivot_price = round(pivot, 2)
            result.stop_loss = round(pivot * 0.92, 2)
        else:
            # 연장(피벗서 5%+ 위) → 매수가 없음, 50일선을 추적 손절선으로
            result.pivot_price = None
            result.stop_loss = round(ma50, 2) if ma50 else None

    return result


def _check_fundamentals(db: Session, stock_id: int, result: ScreeningResult) -> bool:
    """펀더멘털 조건 확인"""
    # 최근 분기 EPS 성장률
    q_funds = (
        db.query(Fundamental)
        .filter(Fundamental.stock_id == stock_id, Fundamental.period_type == "Q")
        .order_by(Fundamental.period_date.desc())
        .limit(5)
        .all()
    )

    if q_funds:
        latest_q = q_funds[0]
        result.latest_q_eps_growth = latest_q.eps_growth_yoy
        result.latest_q_rev_growth = latest_q.revenue_growth_yoy

        q_eps_ok = latest_q.eps_growth_yoy is not None and latest_q.eps_growth_yoy >= 25
        q_rev_ok = latest_q.revenue_growth_yoy is not None and latest_q.revenue_growth_yoy >= 20
    else:
        # 재무 데이터 없으면 기술적 조건만으로 판단 (선택적)
        return True

    # 연간 EPS 우상향 확인 (최근 3년)
    y_funds = (
        db.query(Fundamental)
        .filter(Fundamental.stock_id == stock_id, Fundamental.period_type == "Y")
        .order_by(Fundamental.period_date.desc())
        .limit(4)
        .all()
    )

    annual_uptrend = False
    if len(y_funds) >= 3:
        eps_vals = [f.eps for f in reversed(y_funds[:3]) if f.eps is not None]
        if len(eps_vals) >= 3:
            annual_uptrend = eps_vals[0] < eps_vals[1] < eps_vals[2]

    result.annual_eps_uptrend = annual_uptrend

    return q_eps_ok and q_rev_ok and annual_uptrend


def detect_vcp(series: pd.Series, min_contractions: int = 3) -> dict:
    """
    VCP(변동성 축소 패턴) 탐지.

    고점→저점 조정 구간을 지그재그로 뽑은 뒤, 조정폭이 뒤로 갈수록 '전체적으로'
    조여드는지 본다. 엄격한 단조 감소(매 구간이 예외 없이 더 작아야 함)는 실제
    스윙 데이터의 잡음 탓에 정상 VCP도 대부분 탈락시키므로 쓰지 않는다. 대신
    Minervini의 2~6회 수축(단계가 점점 얕아지고 마지막이 가장 타이트)에 맞춰
    세 가지 질적 조건으로 판정한다:
      1) 마지막 조정 ≤ 가장 넓은 조정의 60%  (전체적으로 조여듦)
      2) 마지막 조정 ≤ 12%                    (돌파 직전 베이스가 타이트)
      3) 다시 벌어진 구간(uptick)은 최대 1회까지 노이즈로 허용
    """
    if len(series) < 60:
        return {"detected": False, "contractions": 0, "pivot": None}

    # 최근 120거래일 사용
    prices = series.tail(120).values
    n = len(prices)
    w = 5

    # 1) 스윙 고점/저점 후보 (윈도우 5)
    raw = []
    for i in range(w, n - w):
        window = prices[i - w:i + w + 1]
        if prices[i] == window.max():
            raw.append((i, float(prices[i]), "H"))
        elif prices[i] == window.min():
            raw.append((i, float(prices[i]), "L"))

    # 2) 고-저-고-저 교대 지그재그로 정리 (연속 동일 타입은 더 극단값만 유지)
    zz: list[tuple[int, float, str]] = []
    for idx, price, kind in raw:
        if zz and zz[-1][2] == kind:
            more_extreme = price > zz[-1][1] if kind == "H" else price < zz[-1][1]
            if more_extreme:
                zz[-1] = (idx, price, kind)
        else:
            zz.append((idx, price, kind))

    # 피벗 = 가장 최근 스윙 고점 (돌파해야 할 매수 트리거 가격)
    pivot = next((p for _, p, k in reversed(zz) if k == "H"), None)
    # 베이스 저점 = 가장 최근 스윙 저점(현재 조정의 바닥) — 게이트/리셋 판정 기준.
    # 120일 전체 최저점을 쓰면 상승 종목에선 늘 ma150 아래라 게이트가 무의미해진다.
    last_low = next((p for _, p, k in reversed(zz) if k == "L"), None)
    base_low = round(last_low, 4) if last_low is not None else None
    base_high = round(pivot, 4) if pivot is not None else None

    # 3) 조정폭(%) = 각 고점 → 바로 다음 저점 낙폭
    corrections = []
    for i in range(len(zz) - 1):
        if zz[i][2] == "H" and zz[i + 1][2] == "L":
            h_price, l_price = zz[i][1], zz[i + 1][1]
            if h_price > 0 and h_price > l_price:
                corrections.append((h_price - l_price) / h_price * 100)

    if len(corrections) < min_contractions:
        return {"detected": False, "contractions": len(corrections), "pivot": pivot,
                "base_high": base_high, "base_low": base_low}

    widest = max(corrections)
    last = corrections[-1]
    upticks = sum(1 for i in range(len(corrections) - 1) if corrections[i + 1] > corrections[i])

    detected = bool(last <= widest * 0.6 and last <= 12.0 and upticks <= 1)

    return {
        "detected": detected,
        "contractions": len(corrections),
        "correction_pcts": [round(c, 1) for c in corrections],
        "pivot": pivot,
        "base_high": base_high,
        "base_low": base_low,
        "widest_pct": round(widest, 1),
        "last_pct": round(last, 1),
    }


def detect_pivot(series: pd.Series, lookback: int = 60, exclude: int = 5) -> float | None:
    """
    베이스(횡보 구간) 상단 = 돌파선(pivot)을 VCP보다 너그럽게 탐지.
    VCP가 아니어도 '고점 근처에서 타이트하게 횡보 중인 베이스'면 피벗을 잡아
    돌파 임박 주도주가 '돌파 대기 / 적극 매수'로 표면화되게 한다.
    """
    if len(series) < lookback + exclude:
        return None
    base = series.iloc[-(lookback + exclude):-exclude]  # 최근 exclude일 제외한 베이스 구간
    if len(base) < 20:
        return None
    base_high = float(base.max())
    base_low = float(base.min())
    if base_high <= 0:
        return None
    depth = (base_high - base_low) / base_high          # 베이스 깊이
    close = float(series.iloc[-1])

    # 베이스 우측(돌파 직전) 타이트함 = 매도세 소진. ★돌파 당일은 base에서 이미 제외됨
    base_right = base.tail(15)
    rng = (float(base_right.max()) - float(base_right.min())) / float(base_right.max()) if len(base_right) and base_right.max() else 1.0

    # 베이스가 과도하게 깊지 않고(≤35%), 돌파 직전이 타이트(≤15%)하며,
    # 현재가가 돌파선 근처~약간 위(0.85×~1.10×)면 유효한 피벗
    if depth <= 0.35 and rng <= 0.15 and base_high * 0.85 <= close <= base_high * 1.10:
        return round(base_high, 2)
    return None


# ─── 매수/매도 신호 (Minervini 매매 규칙) ───
SIGNAL_LABELS = {
    "STRONG_BUY": "적극 매수",
    "BUY": "매수",
    "WATCH": "관심",
    "SELL": "매도",
    "AVOID": "회피",
}


def compute_signal(result: ScreeningResult, pivot: float | None, market: str = "US") -> tuple[str, str]:
    """
    스크리닝 결과로부터 매수/매도 의견 산출.
    Minervini 규칙: 추세가 살아있을 때만 매수, 추세 이탈 시 매도.
    """
    close = result.close
    ma50, ma150, ma200 = result.ma50, result.ma150, result.ma200

    def c(v):  # 시장별 통화 표기
        return f"${v:,.2f}" if market == "US" else f"₩{v:,.0f}"

    # 1) 추세 자체가 약세 → 회피 (매수 대상 아님, 노이즈라 별도 강조 안 함)
    if ma200 and close < ma200:
        return "AVOID", "200일선 아래에서 하락·횡보 중이라 매수를 피하세요"
    if ma150 and ma200 and ma150 < ma200:
        return "AVOID", "이동평균이 역배열(150일선이 200일선 아래)이라 아직 상승 추세가 아닙니다"

    # 2) Stage 2 상승추세는 유지하나 단기 추세 이탈 (50일선 하회)
    #    = '강세였다가 막 무너지기 시작' → 보유자에게 의미 있는 매도 경고
    if ma50 and close < ma50:
        return "SELL", "50일선이 무너지며 추세가 약해졌습니다. 보유 중이라면 매도나 비중 축소를 고려하세요"

    # 3) 추세 정상 → 매수 후보 판별
    if result.final_pass:
        base = "VCP 형성" if result.vcp_detected else "베이스 형성"
        # 아직 피벗 아래 = 돌파 대기
        if pivot and close < pivot:
            gap = (pivot / close - 1) * 100
            return "BUY", f"{base}에 펀더멘털까지 통과했습니다. 피벗 {c(pivot)} 돌파를 기다리세요 (+{gap:.1f}% 남음)"
        # 피벗 갓 돌파(5% 이내) = 적극 매수
        if pivot and close <= pivot * 1.05:
            return "STRONG_BUY", f"피벗 {c(pivot)}을 막 돌파했습니다. 미너비니 기준 매수 시점입니다"
        # 추세·실적은 통과했으나 직전 고점 위로 연장(extended) → 추격 매수 부적절
        return "BUY", "추세와 실적은 통과했지만 고점 위로 많이 올라있습니다. 50일선까지 눌림목을 기다리세요"

    if result.technical_pass:
        return "WATCH", "추세는 좋지만 실적 모멘텀이 아직 부족합니다. 관심 종목으로 지켜보세요"

    return "WATCH", "Stage 2 조건을 아직 다 채우지 못했습니다. 추세가 자리 잡을 때까지 기다리세요"


# 포지션 사이징 가정값 (예시용)
_EXAMPLE_ACCOUNT = 10_000          # 예시 계좌 규모 (미국, $)
_EXAMPLE_ACCOUNT_KR = 10_000_000   # 예시 계좌 규모 (한국, ₩ 1천만원)
_ACCOUNT_RISK_PCT = 1.25       # 한 종목에 거는 계좌 리스크 (Minervini는 보통 1.25~2.5%)
_MAX_WEIGHT_PCT = 25.0         # 단일 종목 최대 비중 상한
_PROFIT_R_MULTIPLE = 2.5       # 1차 익절 목표 = 리스크의 2.5배 (손익비)


def build_trade_plan(result: ScreeningResult, market: str = "US") -> dict | None:
    """
    매수 의견(STRONG_BUY/BUY)일 때 Minervini식 진입·손절·분할·매도 플레이북 생성.
    매수 신호가 아니면 None. market에 따라 통화($/₩)와 예시 계좌 규모를 분기.
    """
    if result.signal not in ("STRONG_BUY", "BUY"):
        return None

    is_us = market == "US"

    def c(v) -> str:  # 통화 포맷
        if v is None:
            return "-"
        return f"${v:,.2f}" if is_us else f"₩{v:,.0f}"

    close = result.close or 0.0
    pivot = result.pivot_price
    ma50 = result.ma50
    stop = result.stop_loss

    # ─── 상황(모드) 판별 ───
    if result.signal == "STRONG_BUY" and pivot:
        mode = "breakout_now"
        headline = "지금이 매수 시점 — 피벗을 갓 돌파했습니다"
        entry = close                       # 피벗 막 돌파 → 현재가 부근 진입
    elif pivot and close < pivot:
        mode = "wait_pivot"
        headline = f"매수 대기 — 피벗 {c(pivot)} 돌파를 확인하고 진입하세요"
        entry = pivot                       # 아직 피벗 아래 → 돌파 시 진입
    else:
        mode = "pullback"
        headline = "추격 금지 — 50일선 눌림목을 기다려 진입하세요"
        entry = ma50                        # 연장 구간 → 눌림목(50일선) 대기
        if ma50:
            stop = round(ma50 * 0.92, 2)    # 50일선 -8%

    risk_pct = None
    if entry and stop and entry > stop:
        risk_pct = round((entry - stop) / entry * 100, 1)

    target = round(entry * (1 + (risk_pct / 100) * _PROFIT_R_MULTIPLE), 2) if (entry and risk_pct) else None
    gap_to_pivot = round((pivot / close - 1) * 100, 1) if (mode == "wait_pivot" and pivot and close) else None

    # ─── 포지션 사이징 예시 (계좌 리스크 1.25%) ───
    sizing = None
    if risk_pct and risk_pct > 0 and entry:
        account = _EXAMPLE_ACCOUNT if is_us else _EXAMPLE_ACCOUNT_KR
        weight_pct = min(_ACCOUNT_RISK_PCT / risk_pct * 100, _MAX_WEIGHT_PCT)
        position = account * weight_pct / 100
        sizing = {
            "account": account,
            "account_risk_pct": _ACCOUNT_RISK_PCT,
            "max_loss": round(account * _ACCOUNT_RISK_PCT / 100),
            "weight_pct": round(weight_pct, 1),
            "position": round(position),
            "shares": int(position // entry),
            "capped": weight_pct >= _MAX_WEIGHT_PCT,
            "market": market,
        }

    # ─── 단계별 진입 절차 ───
    if mode == "breakout_now":
        steps = [
            "거래량이 평균 대비 40% 이상 늘었는지 확인 (거래량 없는 돌파는 신뢰도 낮음).",
            f"현재가 {c(entry)} 부근 진입 — 피벗 대비 +5% 이상 연장되면 추격 금지.",
            f"손절 {c(stop)} (-{risk_pct}%) 즉시 설정 — 예외 없음.",
        ]
    elif mode == "wait_pivot":
        steps = [
            f"피벗까지 +{gap_to_pivot}% — 아직 매수 보류, 돌파 여부를 지켜보세요.",
            f"피벗 {c(pivot)}을 거래량 동반해 돌파하면 {c(entry)} 부근에서 진입.",
            f"진입과 동시에 손절 {c(stop)} (-{risk_pct}%) 설정.",
        ]
    else:  # pullback
        steps = [
            "고점 위로 연장된 상태 — 지금 추격하면 손절폭 과다.",
            f"50일선({c(ma50)}) 눌림에서 거래량이 줄며 지지되는지 확인한 후 진입.",
            f"손절은 50일선 아래 {c(stop)} 부근(-{risk_pct}%)에 설정." if risk_pct else "손절은 50일선 살짝 아래에 설정.",
        ]

    # ─── 공통 매도/관리 규칙 ───
    sell_rules = [
        f"손절 {c(stop)} 이탈 시 즉시 전량 매도 (Minervini 제1원칙).",
    ]
    if target:
        sell_rules.append(
            f"+{round(risk_pct * _PROFIT_R_MULTIPLE, 1)}% (목표 {c(target)}) 도달 시 일부 익절, 나머지는 본전 손절로 전환."
        )
    sell_rules += [
        "50일선을 추적 손절선으로 사용 — 거래량을 동반하며 종가가 이탈하면 정리.",
        "이익이 본전까지 줄면 청산 (손실 전환 방지).",
    ]

    return {
        "mode": mode,
        "headline": headline,
        "entry": round(entry, 2) if entry else None,
        "stop": round(stop, 2) if stop else None,
        "risk_pct": risk_pct,
        "target": target,
        "reward_pct": round(risk_pct * _PROFIT_R_MULTIPLE, 1) if risk_pct else None,
        "gap_to_pivot": gap_to_pivot,
        "sizing": sizing,
        "steps": steps,
        "sell_rules": sell_rules,
    }


def compute_market_breadth(db: Session, screen_date: date, market: str | None = None) -> dict:
    """
    스크리닝 결과로 시장 국면(breadth) 판정. market 지정 시 해당 시장만 집계.
    Minervini: 개별 종목의 약 3/4는 시장 전체를 따라간다 → 시장이 건강할 때만 적극 매수.
    """
    q = (
        db.query(
            ScreeningResult.cond_price_above_ma200,
            ScreeningResult.cond_price_above_ma50,
            ScreeningResult.technical_pass,
        )
        .filter(ScreeningResult.screen_date == screen_date)
    )
    if market:
        q = q.join(Stock, ScreeningResult.stock_id == Stock.id).filter(Stock.market == market)
    rows = q.all()
    total = len(rows)
    if total == 0:
        return {"available": False}

    pct_200 = sum(1 for r in rows if r[0]) / total * 100
    pct_50 = sum(1 for r in rows if r[1]) / total * 100
    pct_stage2 = sum(1 for r in rows if r[2]) / total * 100

    if pct_200 >= 60 and pct_50 >= 50:
        regime, label = "BULL", "강세장 — 적극 매수 가능"
        color = {"light": "#089981", "dark": "#26a69a"}
        advice = "추세가 건강합니다 — 매수 신호를 활용하되 손절·사이징 규칙은 지키세요."
    elif pct_200 < 40:
        regime, label = "BEAR", "약세장 — 신규 매수 자제"
        color = {"light": "#f23645", "dark": "#ef5350"}
        advice = "대부분 종목이 하락하는 구간(Minervini) — 현금 비중을 높이고 신규 진입을 줄이세요."
    else:
        regime, label = "NEUTRAL", "중립 — 선별적 접근"
        color = {"light": "#fb8c00", "dark": "#ffa726"}
        advice = "혼조 구간 — 가장 강한 소수 종목만 작은 비중으로 시험 매수하세요."

    return {
        "available": True,
        "regime": regime,
        "label": label,
        "color": color,
        "advice": advice,
        "pct_above_200": round(pct_200),
        "pct_above_50": round(pct_50),
        "pct_stage2": round(pct_stage2),
        "total": total,
    }


def apply_regime_gate(db: Session, screen_date: date) -> int:
    """시장 국면 게이트 — 약세장(BEAR)인 시장의 매수신호를 보류(WATCH)로 강등.

    Minervini: 약세장에선 대부분 종목이 시장을 따라 하락한다. 백테스트(시점복원,
    2020~2026)에서도 지수<200MA 국면의 트렌드 신호는 시장평균 대비 1·3·6개월
    각 -2.2/-2.5/-3.5%p로 열위였다 → 기술적으론 매수신호여도 시장이 약하면 보류.

    원래 신호는 signal_reason에 남겨 투명하게 표시(🚫 표식). 종목 자체의 기술
    조건은 그대로라, 시장이 회복되면 다음 스크리닝에서 다시 매수신호로 복귀한다.
    """
    if not settings.REGIME_GATE:
        return 0
    markets = {m for (m,) in db.query(Stock.market).distinct() if m}
    gated = 0
    for mk in markets:
        breadth = compute_market_breadth(db, screen_date, market=mk)
        if not breadth.get("available") or breadth["regime"] != "BEAR":
            continue
        rows = (
            db.query(ScreeningResult)
            .join(Stock, ScreeningResult.stock_id == Stock.id)
            .filter(
                ScreeningResult.screen_date == screen_date,
                Stock.market == mk,
                ScreeningResult.signal.in_(["STRONG_BUY", "BUY"]),
            )
            .all()
        )
        for r in rows:
            orig = SIGNAL_LABELS.get(r.signal, r.signal)
            r.signal = "WATCH"
            r.signal_reason = f"🚫 약세장이라 매수 신호를 보류했습니다. 기술적으로는 '{orig}' 신호이며, 시장이 회복되면 다시 나타납니다"
            r.pivot_price = None   # 약세장에선 진입가 제시 안 함
            r.stop_loss = None
            gated += 1
    if gated:
        db.commit()
        logger.info(f"시장 국면 게이트: 약세장 시장의 매수신호 {gated}건 보류(WATCH)")
    return gated


VCP_STALE_DAYS = 15   # VCP가 이만큼 재확인 안 되고 돌파도 없으면 실패로 확정


def _migrate_schema():
    """새 테이블(vcp_events)·신규 컬럼(vcp_pivot) 보장. init_db가 create_all+ALTER 수행."""
    from app.database import init_db
    init_db()


def _trend_ok(r: ScreeningResult) -> bool:
    """상승추세(Stage 2) 하드 게이트 — 이미 저장된 cond_* 6개만 AND(새 계산 없음).

    52주고가 근접·펀더멘털은 제외한다(정상적인 깊은 베이스를 탈락시키고 forming을
    하루 단위로 깜빡이게 해 first_detected를 오염시키므로). 그것들은 품질점수 입력으로만.
    """
    return bool(
        r.cond_price_above_ma150 and r.cond_price_above_ma200
        and r.cond_ma150_above_ma200 and r.cond_ma200_uptrend
        and r.cond_price_above_ma50 and r.cond_rs_rank
    )


def _vcp_quality(r: ScreeningResult, base_days: int, base_seq: int) -> int:
    """VCP 품질점수(0~100). 추적은 관대하게, 알림/랭킹은 이 점수로 선별.

    RS·최종수축 타이트함·거래량 마름·수축 횟수·형성 기간을 가중하고, 후행 베이스(3차+)는
    Minervini의 높은 실패율을 반영해 감점. 초기 휴리스틱 — 보존 데이터로 캘리브레이션 예정.
    """
    rs = min(r.rs_rank or 0.0, 100.0)
    last = r.vcp_last_contraction
    tight = max(0.0, min(100.0, (12.0 - last) / (12.0 - 3.0) * 100)) if last is not None else 0.0
    c = r.vcp_contractions or 0
    q = (
        0.35 * rs
        + 0.25 * tight
        + (15 if r.vcp_volume_dryup else 0)
        + (10 if 3 <= c <= 5 else (5 if c >= 2 else 0))
        + (10 if base_days >= 15 else 0)
        - (15 if base_seq >= 3 else 0)
    )
    return int(round(max(0.0, min(100.0, q))))


def update_vcp_registry(db: Session, screen_date: date) -> dict:
    """오늘 스크리닝 결과로 VCP 레지스트리(vcp_events)를 갱신.

    한 VCP 베이스를 한 행에 '최초발생일(=첫 형성)~최근일'로 누적하고 상태를 확정한다:
      - 종가 ≥ 피벗 & 거래량 동반(또는 피벗 위 N일 유지) → broke_out 확정,
        그 전까진 breakout_watch로 격리(무한 대기 방지)
      - 적격(추세+진짜 VCP+베이스 150일선 위)일 때만: 베이스 floor를 깬 더 깊은 저점 →
        reset 후 새 베이스(first_detected 재기록), 아니면 forming 갱신/신규
      - 미적격(추세 이탈 등) grace 초과 or STALE_DAYS 방치 → failed 확정
    리셋은 추세·적격성 검사 이후에만 판정하므로, 하루짜리 추세 깜빡임은 리셋이 아니라
    grace로 흡수된다. 등록은 진짜 VCP(vcp_detected)만 적재(detect_pivot 폴백 미사용).
    """
    from app.models import VCPEvent

    reset_undercut = settings.VCP_RESET_UNDERCUT
    grace_days = settings.VCP_TREND_GRACE_DAYS
    breakout_vol = settings.VCP_BREAKOUT_VOL
    breakout_watch_days = settings.VCP_BREAKOUT_WATCH_DAYS

    results = db.query(ScreeningResult).filter(ScreeningResult.screen_date == screen_date).all()
    open_events = {
        e.stock_id: e
        for e in db.query(VCPEvent).filter(VCPEvent.status.in_(["forming", "breakout_watch"])).all()
    }
    stats = {"new": 0, "updated": 0, "broke_out": 0, "watch": 0, "reset": 0, "failed": 0}
    seen = set()

    def _snapshot(ev, r, base_seq):
        ev.last_detected = screen_date
        ev.contractions = r.vcp_contractions
        ev.pivot_price = r.vcp_pivot or ev.pivot_price
        ev.stop_loss = round(ev.pivot_price * 0.92, 2) if ev.pivot_price else None
        # base_low는 베이스 '바닥(floor)'을 추적한다 — 최근 스윙 저점으로 덮어쓰면(ratchet)
        # 임계가 계속 올라가 정상적인 눌림에도 리셋이 오발동한다. 더 깊은 저점만 반영(min).
        if r.vcp_base_low is not None:
            ev.base_low = r.vcp_base_low if ev.base_low is None else min(ev.base_low, r.vcp_base_low)
        if r.vcp_base_high is not None:
            ev.base_high = r.vcp_base_high
        ev.rs_rank = r.rs_rank
        ev.close = r.close
        ev.volume_dryup = bool(r.vcp_volume_dryup)
        ev.base_seq = base_seq
        ev.trend_grace = 0
        base_days = (screen_date - ev.first_detected).days
        ev.quality = _vcp_quality(r, base_days, base_seq)
        ev.peak_quality = max(ev.peak_quality or 0, ev.quality)

    def _new_event(r, base_seq):
        pivot = r.vcp_pivot
        ev = VCPEvent(
            stock_id=r.stock_id, first_detected=screen_date, last_detected=screen_date,
            status="forming", contractions=r.vcp_contractions,
            pivot_price=pivot, stop_loss=round(pivot * 0.92, 2) if pivot else None,
            base_low=r.vcp_base_low, base_high=r.vcp_base_high, base_seq=base_seq,
            trend_grace=0, rs_rank=r.rs_rank, close=r.close,
            volume_dryup=bool(r.vcp_volume_dryup),
        )
        ev.quality = _vcp_quality(r, 0, base_seq)
        ev.peak_quality = ev.quality
        db.add(ev)

    for r in results:
        seen.add(r.stock_id)
        ev = open_events.get(r.stock_id)
        trend = _trend_ok(r)
        # 등록 자격: 추세 게이트 + 진짜 VCP + 베이스가 150일선 위(급락 중 얕은 되돌림 배제)
        eligible = bool(
            trend and r.vcp_detected and r.vcp_base_low is not None
            and r.ma150 and r.vcp_base_low >= r.ma150
        )

        # 1) 돌파 확인 — 거래량 동반이 원칙. 거래량 미달이면 breakout_watch로 격리하되,
        #    피벗 위에서 N일 버티면(느린 돌파) 돌파로 확정해 무한 대기/유실을 막는다.
        if ev and ev.pivot_price and r.close and r.close >= ev.pivot_price:
            vol_ok = (r.vol_vs_avg or 0) >= breakout_vol
            held_long = (ev.status == "breakout_watch"
                         and (screen_date - ev.last_detected).days >= breakout_watch_days)
            if vol_ok or held_long:
                ev.status = "broke_out"
                ev.breakout_date = screen_date
                ev.breakout_price = r.close
                ev.resolved_date = screen_date
                ev.close = r.close
                ev.rs_rank = r.rs_rank
                stats["broke_out"] += 1
            else:
                # 관찰 진입일(last_detected)을 만료 카운터로 쓴다 — 진입 시 1회만 찍고
                # 이후엔 갱신하지 않아야 위 held_long 카운트가 실제로 흐른다.
                if ev.status != "breakout_watch":
                    ev.last_detected = screen_date
                    stats["watch"] += 1
                ev.status = "breakout_watch"
                ev.close = r.close
            continue

        # breakout_watch였는데 피벗 아래로 되돌림 → forming 복귀
        if ev and ev.status == "breakout_watch" and r.close and ev.pivot_price and r.close < ev.pivot_price * 0.97:
            ev.status = "forming"

        # 2) 적격(추세+진짜 VCP+베이스 150일선 위)일 때만 리셋/갱신을 판정한다.
        #    리셋 검사를 추세·적격성 검사 '이후'에 둬, 추세 깜빡임은 아래 3)의 grace가 흡수하고
        #    베이스 floor를 실제로 깬 경우에만 리셋으로 새 베이스를 연다.
        if eligible:
            # 2a) 베이스 floor를 undercut한 더 깊은 저점 → 리셋 후 새 베이스(first_detected 재기록)
            if ev and ev.base_low and r.vcp_base_low < ev.base_low * (1 - reset_undercut):
                ev.status = "reset"
                ev.resolved_date = screen_date
                ev.close = r.close
                stats["reset"] += 1
                _new_event(r, (ev.base_seq or 1) + 1)
                stats["new"] += 1
                continue
            # 2b) 기존 갱신 / 신규
            if ev:
                if ev.status == "breakout_watch":
                    ev.status = "forming"
                _snapshot(ev, r, ev.base_seq or 1)
                stats["updated"] += 1
            else:
                _new_event(r, 1)
                stats["new"] += 1
            continue

        # 3) 미적격(미탐지/게이트 이탈) → grace 누적, 초과 or STALE 방치 시 실패 확정
        if ev:
            if not trend:
                ev.trend_grace = (ev.trend_grace or 0) + 1
            stale = (screen_date - ev.last_detected).days > VCP_STALE_DAYS
            if (ev.trend_grace or 0) > grace_days or stale:
                ev.status = "failed"
                ev.resolved_date = screen_date
                ev.close = r.close
                stats["failed"] += 1

    # 스크리닝에서 빠진 종목(비활성 등)의 오래된 열린 이벤트도 실패 처리
    for sid, ev in open_events.items():
        if sid not in seen and (screen_date - ev.last_detected).days > VCP_STALE_DAYS:
            ev.status = "failed"
            ev.resolved_date = screen_date

    db.commit()
    logger.info(f"VCP 레지스트리: 신규 {stats['new']}, 갱신 {stats['updated']}, 돌파 {stats['broke_out']}, "
                f"돌파대기 {stats['watch']}, 리셋 {stats['reset']}, 실패 {stats['failed']}")
    return stats


def prune_old_history(db: Session, keep_price_days: int = 450, keep_screen_days: int = 90) -> None:
    """배포 스냅샷(screener.db)의 무한 증가를 막기 위해 오래된 행을 정리한다.

    매일 일봉 ~800행이 영구 누적되면 커밋 blob이 계속 커진다. 스크리너의 최대
    lookback(RS 52주 + 10일 ≈ 374일)보다 넉넉히 보존(기본 450일)하면 신호에 영향이
    없다. DELETE만 하고 VACUUM은 하지 않는다 — 비워진 페이지는 다음 일봉 insert가
    재사용하므로 파일 크기가 자연히 안정화된다(일회성 축소는 VACUUM으로 별도 수행).
    """
    latest = db.query(DailyPrice.date).order_by(DailyPrice.date.desc()).first()
    if not latest:
        return
    latest = latest[0]
    n_price = (
        db.query(DailyPrice)
        .filter(DailyPrice.date < latest - timedelta(days=keep_price_days))
        .delete(synchronize_session=False)
    )
    n_screen = (
        db.query(ScreeningResult)
        .filter(ScreeningResult.screen_date < latest - timedelta(days=keep_screen_days))
        .delete(synchronize_session=False)
    )
    # VCP 이벤트: 진행 중(forming/breakout_watch)은 영구 보존, 완료 건(broke_out/failed/reset)만 180일 후 정리
    from app.models import VCPEvent
    n_vcp = (
        db.query(VCPEvent)
        .filter(
            VCPEvent.status.notin_(["forming", "breakout_watch"]),
            VCPEvent.resolved_date.isnot(None),
            VCPEvent.resolved_date < latest - timedelta(days=180),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    if n_price or n_screen or n_vcp:
        logger.info(
            f"오래된 데이터 정리: 일봉 {n_price}행, 스크리닝 {n_screen}행, VCP이벤트 {n_vcp}행 "
            f"(보존 {keep_price_days}/{keep_screen_days}일)"
        )


def run_daily_screen(db: Session) -> int:
    """전체 종목 스크리닝 실행 (일일 배치)"""
    _migrate_schema()   # vcp_events 테이블·vcp_pivot 컬럼 보장(기존 DB용)
    screen_date = date.today()
    stocks = db.query(Stock).filter(Stock.is_active == True).all()

    if not stocks:
        logger.warning("스크리닝할 종목이 없습니다.")
        return 0

    logger.info(f"스크리닝 시작: {len(stocks)}개 종목, 기준일 {screen_date}")

    # RS 점수는 시장별로 따로 계산 (미국 vs 한국 혼합 방지)
    rs_scores: dict[int, float] = {}
    markets = {s.market or "US" for s in stocks}
    for mk in markets:
        ids = {s.id for s in stocks if (s.market or "US") == mk}
        rs_scores.update(_calc_rs_scores(db, screen_date, stock_ids=ids))

    passed = 0
    for stock in stocks:
        # 기존 결과 삭제 후 재계산
        db.query(ScreeningResult).filter(
            ScreeningResult.stock_id == stock.id,
            ScreeningResult.screen_date == screen_date,
        ).delete()

        rs_rank = rs_scores.get(stock.id, 0.0)
        result = screen_stock(db, stock, screen_date, rs_rank)
        db.add(result)

        if result.final_pass:
            passed += 1

    db.commit()
    logger.info(f"스크리닝 완료: {passed}/{len(stocks)} 종목 통과")

    # 시장 국면 게이트 — 약세장 시장의 매수신호 보류(WATCH)
    apply_regime_gate(db, screen_date)

    # VCP 레지스트리 갱신 (발생·돌파·실패 이력 누적)
    update_vcp_registry(db, screen_date)

    # 스냅샷 DB 무한 증가 방지 (오래된 일봉/스크리닝 결과 정리)
    prune_old_history(db)
    return passed
