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


def _calc_rs_scores(db: Session, screen_date: date) -> dict[int, float]:
    """
    RS(상대강도) 계산: 52주 수익률 기준 전 종목 대비 백분위 (0~100, 높을수록 강함)
    Minervini는 IBD RS Line과 유사하게 52주 퍼포먼스를 사용
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
    pivot = vcp_result.get("pivot")

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
        # 유동성: 최소 주가 & 최소 평균 거래량 충족 (페니/저유동 제외)
        result.liquidity_pass = bool(close >= settings.MIN_PRICE and avg_vol_50 >= settings.MIN_VOLUME)
    else:
        result.liquidity_pass = True  # 거래량 데이터 부족 시 배제하지 않음

    result.final_pass = result.technical_pass and result.fundamental_pass and result.liquidity_pass

    # ─── 매수/매도 의견 산출 ───
    signal, reason = compute_signal(result, pivot)
    result.signal = signal
    result.signal_reason = reason
    if signal in ("STRONG_BUY", "BUY"):
        if result.vcp_detected and pivot and close <= pivot * 1.05:
            # 유효한 돌파 매수가: 피벗 + 손절 -8% (Minervini 7~8%)
            result.pivot_price = round(pivot, 2)
            result.stop_loss = round(pivot * 0.92, 2)
        else:
            # 연장 or VCP 미형성 → 매수가 없음, 50일선을 추적 손절선으로
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
    VCP(변동성 축소 패턴) 탐지
    최근 가격 시리즈에서 고점-저점 사이클의 조정폭이 줄어드는지 확인
    """
    if len(series) < 60:
        return {"detected": False, "contractions": 0}

    # 최근 120거래일 사용
    recent = series.tail(120)
    prices = recent.values
    n = len(prices)

    # 스윙 고점/저점 탐지 (윈도우 5)
    swing_highs = []
    swing_lows = []
    w = 5
    for i in range(w, n - w):
        if prices[i] == max(prices[i - w:i + w + 1]):
            swing_highs.append((i, prices[i]))
        if prices[i] == min(prices[i - w:i + w + 1]):
            swing_lows.append((i, prices[i]))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"detected": False, "contractions": 0}

    # 고점-저점 쌍으로 조정폭(%) 계산
    corrections = []
    for i in range(min(len(swing_highs), len(swing_lows)) - 1):
        h_idx, h_price = swing_highs[i]
        # 이 고점 이후 나타나는 저점
        lows_after = [(li, lp) for li, lp in swing_lows if li > h_idx]
        if not lows_after:
            continue
        l_idx, l_price = lows_after[0]
        correction_pct = (h_price - l_price) / h_price * 100
        if correction_pct > 0:  # 진짜 하락 조정만 포함
            corrections.append(correction_pct)

    # 피벗 = 가장 최근 스윙 고점 (돌파해야 할 매수 트리거 가격)
    pivot = float(swing_highs[-1][1]) if swing_highs else None

    if len(corrections) < min_contractions:
        return {"detected": False, "contractions": len(corrections), "pivot": pivot}

    # 조정폭이 왼쪽에서 오른쪽으로 줄어드는지 확인
    is_contracting = all(corrections[i] > corrections[i + 1] for i in range(len(corrections) - 1))

    return {
        "detected": is_contracting,
        "contractions": len(corrections),
        "correction_pcts": [round(c, 1) for c in corrections],
        "pivot": pivot,
    }


# ─── 매수/매도 신호 (Minervini 매매 규칙) ───
SIGNAL_LABELS = {
    "STRONG_BUY": "적극 매수",
    "BUY": "매수",
    "WATCH": "관심",
    "SELL": "매도",
    "AVOID": "회피",
}


def compute_signal(result: ScreeningResult, pivot: float | None) -> tuple[str, str]:
    """
    스크리닝 결과로부터 매수/매도 의견 산출.
    Minervini 규칙: 추세가 살아있을 때만 매수, 추세 이탈 시 매도.
    """
    close = result.close
    ma50, ma150, ma200 = result.ma50, result.ma150, result.ma200

    # 1) 추세 자체가 약세 → 회피 (매수 대상 아님, 노이즈라 별도 강조 안 함)
    if ma200 and close < ma200:
        return "AVOID", "현재가가 200일선 아래 — 하락/횡보 구간, 매수 회피"
    if ma150 and ma200 and ma150 < ma200:
        return "AVOID", "150일선 < 200일선 (이동평균 역배열) — 추세 미형성"

    # 2) Stage 2 상승추세는 유지하나 단기 추세 이탈 (50일선 하회)
    #    = '강세였다가 막 무너지기 시작' → 보유자에게 의미 있는 매도 경고
    if ma50 and close < ma50:
        return "SELL", "50일선 하향 이탈 — 상승추세 약화 조짐, 보유 시 매도/축소 고려"

    # 3) 추세 정상 → 매수 후보 판별
    if result.final_pass:
        # 피벗은 '아직 안 깬 돌파선' 또는 '갓 돌파(5% 이내)'일 때만 매수가로 의미 있음
        if result.vcp_detected and pivot and close < pivot:
            gap = (pivot / close - 1) * 100
            return "BUY", f"VCP 형성 + 펀더멘털 통과 — 피벗 ${pivot:.2f} 돌파 대기 (+{gap:.1f}%)"
        if result.vcp_detected and pivot and close <= pivot * 1.05:
            return "STRONG_BUY", f"VCP 피벗(${pivot:.2f}) 갓 돌파 — 미너비니 매수 시점"
        # 추세·실적은 통과했으나 직전 고점 위로 연장(extended) → 추격 매수 부적절
        return "BUY", "추세+실적 통과했으나 직전 고점 위로 연장 — 눌림목(50일선) 대기 권고"

    if result.technical_pass:
        return "WATCH", "기술적 추세 양호하나 실적 모멘텀 부족 — 관심 종목"

    return "WATCH", "Stage 2 일부 조건 미충족 — 추세 형성 대기"


# 포지션 사이징 가정값 (예시용)
_EXAMPLE_ACCOUNT = 10_000      # 예시 계좌 규모 ($)
_ACCOUNT_RISK_PCT = 1.25       # 한 종목에 거는 계좌 리스크 (Minervini는 보통 1.25~2.5%)
_MAX_WEIGHT_PCT = 25.0         # 단일 종목 최대 비중 상한
_PROFIT_R_MULTIPLE = 2.5       # 1차 익절 목표 = 리스크의 2.5배 (손익비)


def build_trade_plan(result: ScreeningResult) -> dict | None:
    """
    매수 의견(STRONG_BUY/BUY)일 때 Minervini식 진입·손절·분할·매도 플레이북 생성.
    매수 신호가 아니면 None.
    """
    if result.signal not in ("STRONG_BUY", "BUY"):
        return None

    close = result.close or 0.0
    pivot = result.pivot_price
    ma50 = result.ma50
    stop = result.stop_loss

    # ─── 상황(모드) 판별 ───
    if result.signal == "STRONG_BUY" and pivot:
        mode = "breakout_now"
        headline = "지금이 매수 구간 — 피벗을 갓 돌파했습니다"
        entry = close                       # 피벗 막 돌파 → 현재가 부근 진입
    elif pivot and close < pivot:
        mode = "wait_pivot"
        headline = f"매수 대기 — 피벗 ${pivot:.2f} 돌파를 확인하고 진입"
        entry = pivot                       # 아직 피벗 아래 → 돌파 시 진입
    else:
        mode = "pullback"
        headline = "추격 금지 — 50일선 눌림목을 기다려 진입"
        entry = ma50                        # 연장 구간 → 눌림목(50일선) 대기
        if ma50:
            stop = round(ma50 * 0.92, 2)    # 50일선 -8%

    risk_pct = None
    if entry and stop and entry > stop:
        risk_pct = round((entry - stop) / entry * 100, 1)

    target = round(entry * (1 + (risk_pct / 100) * _PROFIT_R_MULTIPLE), 2) if (entry and risk_pct) else None
    gap_to_pivot = round((pivot / close - 1) * 100, 1) if (mode == "wait_pivot" and pivot and close) else None

    # ─── 포지션 사이징 예시 ($10,000 계좌, 계좌 리스크 1.25%) ───
    sizing = None
    if risk_pct and risk_pct > 0 and entry:
        weight_pct = min(_ACCOUNT_RISK_PCT / risk_pct * 100, _MAX_WEIGHT_PCT)
        position_dollar = _EXAMPLE_ACCOUNT * weight_pct / 100
        sizing = {
            "account": _EXAMPLE_ACCOUNT,
            "account_risk_pct": _ACCOUNT_RISK_PCT,
            "max_loss_dollar": round(_EXAMPLE_ACCOUNT * _ACCOUNT_RISK_PCT / 100),
            "weight_pct": round(weight_pct, 1),
            "position_dollar": round(position_dollar),
            "shares": int(position_dollar // entry),
            "capped": weight_pct >= _MAX_WEIGHT_PCT,
        }

    # ─── 단계별 진입 절차 ───
    if mode == "breakout_now":
        steps = [
            "돌파 당일 거래량이 평균 대비 크게(40% 이상) 늘었는지 확인 — 거래량 없는 돌파는 신뢰도가 낮습니다.",
            f"현재가 ${entry:.2f} 부근에서 진입. 피벗에서 5% 넘게 연장됐다면 추격하지 말고 다음 기회를 기다리세요.",
            f"진입 즉시 손절 주문 ${stop:.2f} (-{risk_pct}%)를 걸어둡니다 — 예외 없이.",
        ]
    elif mode == "wait_pivot":
        steps = [
            f"아직 피벗 아래입니다(돌파까지 +{gap_to_pivot}%). 매수를 보류하고 관심목록에 둡니다.",
            f"피벗 ${pivot:.2f} 위로 거래량 동반 돌파가 확인되면 그때 ${entry:.2f} 부근에서 진입.",
            f"진입과 동시에 손절 ${stop:.2f} (-{risk_pct}%) 설정.",
        ]
    else:  # pullback
        steps = [
            "추세·실적은 통과했지만 직전 고점 위로 연장된 상태 — 지금 추격하면 손절폭이 너무 커집니다.",
            f"50일선(${ma50:.2f}) 부근까지 눌릴 때 거래량이 줄며 지지받는지 확인 후 진입.",
            f"진입 시 손절은 50일선 아래 ${stop:.2f} 부근(-{risk_pct}%)에 설정." if risk_pct else "진입 시 손절은 50일선 살짝 아래에 설정.",
        ]

    # ─── 공통 매도/관리 규칙 ───
    sell_rules = [
        f"손절가 ${stop:.2f} 이탈 시 즉시 전량 매도 — '조금만 더'는 금물(Minervini의 첫 번째 규칙).",
    ]
    if target:
        sell_rules.append(
            f"+{round(risk_pct * _PROFIT_R_MULTIPLE, 1)}% (목표 ${target:.2f}) 도달 시 일부 익절 → 남은 물량은 본전 손절로 옮겨 '공짜 포지션' 확보."
        )
    sell_rules += [
        "큰 추세는 50일선을 추적 손절선으로 사용 — 50일선을 거래량 동반해 종가로 깨면 정리.",
        "수익을 손실로 바꾸지 않는다 — 이익이 본전까지 줄면 청산.",
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


def compute_market_breadth(db: Session, screen_date: date) -> dict:
    """
    전체 종목 스크리닝 결과로 시장 국면(breadth) 판정.
    Minervini: 개별 종목의 약 3/4는 시장 전체를 따라간다 → 시장이 건강할 때만 적극 매수.
    """
    rows = (
        db.query(
            ScreeningResult.cond_price_above_ma200,
            ScreeningResult.cond_price_above_ma50,
            ScreeningResult.technical_pass,
        )
        .filter(ScreeningResult.screen_date == screen_date)
        .all()
    )
    total = len(rows)
    if total == 0:
        return {"available": False}

    pct_200 = sum(1 for r in rows if r[0]) / total * 100
    pct_50 = sum(1 for r in rows if r[1]) / total * 100
    pct_stage2 = sum(1 for r in rows if r[2]) / total * 100

    if pct_200 >= 60 and pct_50 >= 50:
        regime, label, color = "BULL", "강세장 — 적극 매수 가능", "#22c55e"
        advice = "시장 추세가 건강합니다. 매수 신호를 활용하되 손절·사이징 규칙은 지키세요."
    elif pct_200 < 40:
        regime, label, color = "BEAR", "약세장 — 신규 매수 자제", "#ef4444"
        advice = "약세장에선 대부분 종목이 하락합니다(Minervini). 현금 비중을 높이고 신규 진입을 줄이세요."
    else:
        regime, label, color = "NEUTRAL", "중립 — 선별적 접근", "#f59e0b"
        advice = "혼조 구간입니다. 가장 강한 소수 종목만 작은 비중으로 시험 매수하세요."

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


def run_daily_screen(db: Session) -> int:
    """전체 종목 스크리닝 실행 (일일 배치)"""
    screen_date = date.today()
    stocks = db.query(Stock).filter(Stock.is_active == True).all()

    if not stocks:
        logger.warning("스크리닝할 종목이 없습니다.")
        return 0

    logger.info(f"스크리닝 시작: {len(stocks)}개 종목, 기준일 {screen_date}")

    # RS 점수 일괄 계산
    rs_scores = _calc_rs_scores(db, screen_date)

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
    return passed
