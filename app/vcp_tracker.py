# -*- coding: utf-8 -*-
"""VCP 형성 추적(Stage 2): VCP 점수 모델(app/vcp.py) 결과를 매일 이어 붙여 vcp_formations에 쌓는다.

구 레지스트리(vcp_events, screener.update_vcp_registry)와 다른 점
  신원      베이스 고점 날짜(key_date)가 신원이고 (stock_id, key_date)가 유니크. first_detected는 바뀌지 않고,
            닫힌 형성도 같은 key면 다시 만들지 않는다(실패 뒤 새 형성으로 중복 기록되는 문제를 막는다).
  돌파      종가가 피벗 위면 돌파. 거래량 조건("1.4배 또는 3일 유지")과 breakout_watch 상태는 없다.
            거래량은 비율과 등급(약함~매우 강함)만 기록한다. 돌파 기록은 처음 한 번만 쓰고 바꾸지 않는다.
  상태      WATCH / NEAR_PIVOT(돌파 전) -> BREAKOUT -> FAILED_BREAKOUT(돌파 뒤 종가가 피벗 아래), INVALIDATED(베이스 무너짐 등)

한 종목에 열린 형성은 최대 하나. 입력 행(rows)은 ScreeningResult이거나 같은 속성을 가진 객체(과거 재생용)다:
  stock_id, close, signal, rs_rank, cond_* (추세 게이트), vcp2_score, vcp2_state, vcp2_pivot, vcp2_depths,
  vcp2_key, vcp2_parts, vcp2_grade, vcp2_age
"""
import json
import logging
from bisect import bisect_right
from datetime import date

from sqlalchemy.orm import Session

from app import vcp as vcp_model
from app.models import DailyPrice, ScreeningResult, Stock, VCPFormation
from config import settings

logger = logging.getLogger(__name__)

PRE_STATES = (vcp_model.WATCH, vcp_model.NEAR_PIVOT)
RS_BROKEN_SHARE = 0.3    # 한 시장에서 RS가 비어(0 또는 NULL) 있는 종목이 이 비율을 넘으면 그날 RS 수집이 깨진 것으로 본다(정상일은 5% 안팎)


def pre_state(pivot: float, close: float) -> str:
    """돌파 전 상태: 피벗까지 거리가 가까우면 NEAR_PIVOT."""
    if pivot and close and (pivot - close) / pivot <= vcp_model.NEAR_PIVOT_PCT:
        return vcp_model.NEAR_PIVOT
    return vcp_model.WATCH


def _bars(db: Session, stock_id: int, after: date | None, upto: date) -> list:
    q = db.query(DailyPrice.date, DailyPrice.close, DailyPrice.volume).filter(
        DailyPrice.stock_id == stock_id, DailyPrice.date <= upto)
    if after is not None:
        q = q.filter(DailyPrice.date > after)
    return q.order_by(DailyPrice.date).all()


def _first_close_above(db: Session, stock_id: int, pivot: float, after: date | None, upto: date):
    for d, c, v in _bars(db, stock_id, after, upto):
        if c is not None and c > pivot:
            return d, c, v
    return None


def _avg_volume_before(db: Session, stock_id: int, d: date, n: int = vcp_model.VOL_AVG_DAYS) -> float | None:
    rows = (db.query(DailyPrice.volume).filter(DailyPrice.stock_id == stock_id, DailyPrice.date < d)
            .order_by(DailyPrice.date.desc()).limit(n).all())
    vals = [v for (v,) in rows if v]
    return sum(vals) / len(vals) if len(vals) >= 20 else None


def _bar_back(db: Session, stock_id: int, upto: date, age: int):
    """upto 이전(포함)의 마지막 봉에서 age거래일 전 봉 (날짜, 종가, 거래량)."""
    row = (db.query(DailyPrice.date, DailyPrice.close, DailyPrice.volume)
           .filter(DailyPrice.stock_id == stock_id, DailyPrice.date <= upto)
           .order_by(DailyPrice.date.desc()).offset(age).limit(1).first())
    return row


def _base_extremes(db: Session, stock_id: int, key_date: date, upto: date):
    """베이스 고점 날짜의 고가와, 그날부터 지금까지의 최저 저가(= 베이스 저점)."""
    rows = (db.query(DailyPrice.date, DailyPrice.high, DailyPrice.low)
            .filter(DailyPrice.stock_id == stock_id, DailyPrice.date >= key_date, DailyPrice.date <= upto).all())
    lows = [lo for _, _, lo in rows if lo is not None]
    highs = [h for d, h, _ in rows if d == key_date and h is not None]
    return (highs[0] if highs else None), (min(lows) if lows else None)


def _regime(db: Session, d: date, market: str, cache: dict):
    key = (d, market)
    if key not in cache:
        from app.screener import compute_market_breadth
        cache[key] = compute_market_breadth(db, d, market)
    return cache[key]


def _record_breakout(db: Session, ev: VCPFormation, bar, market: str, regime_cache: dict, score: float | None) -> None:
    """첫 돌파 기록. 이미 있으면 건드리지 않는다."""
    if ev.breakout_date is not None:
        return
    d, close, vol = bar
    avg = _avg_volume_before(db, ev.stock_id, d)
    ev.breakout_date, ev.breakout_price = d, float(close)
    ev.breakout_volume = float(vol) if vol else None
    ev.breakout_avg_volume = avg
    if vol and avg:
        ev.volume_ratio = round(float(vol) / avg, 2)
        ev.volume_quality = vcp_model.QUALITY_LABELS[bisect_right(vcp_model.QUALITY_EDGES, vol / avg)]
    ev.breakout_score = score
    ev.status = vcp_model.BREAKOUT
    reg = _regime(db, d, market, regime_cache)
    if reg and reg.get("available"):
        ev.regime_at_breakout, ev.pct_above_200_at_breakout = reg["regime"], reg["pct_above_200"]


def _end(ev: VCPFormation, screen_date: date, reason: str, status: str | None = None) -> None:
    ev.resolved_date, ev.end_reason = screen_date, reason
    if status:
        ev.status = status


def _refresh(ev: VCPFormation, r, screen_date: date) -> None:
    ev.pivot = r.vcp2_pivot
    ev.depths = r.vcp2_depths
    ev.contractions = len(r.vcp2_depths.split("/")) if r.vcp2_depths else None
    ev.score_last = r.vcp2_score
    ev.score_peak = max(ev.score_peak or 0, r.vcp2_score)
    ev.grade_last = r.vcp2_grade
    ev.parts_last = r.vcp2_parts
    ev.rs_rank = r.rs_rank
    ev.close = r.close
    ev.last_seen = screen_date
    ev.trend_grace = 0
    ev.status = pre_state(ev.pivot, r.close)


def _advance(db: Session, ev: VCPFormation, r, trend: bool, screen_date: date, market: str,
             regime_cache: dict, stats: dict, unreliable: bool = False) -> bool:
    """열린 형성을 오늘 데이터로 한 칸 진행한다. 계속 열려 있으면 True, 이번에 닫았으면 False.

    unreliable: 그 시장의 RS가 대부분 비어 있는 날(수집 실패). 추세 게이트와 점수가 믿을 수 없으므로
    가격으로 판정되는 것(베이스 저점 이탈, 돌파, 돌파 뒤 추적)만 처리하고 나머지는 건드리지 않는다."""
    close = r.close
    if close is None:
        return True
    ev.close = close

    # ── 돌파 뒤: 가격만으로 돌파/실패를 따라가고, 일정 거래일이 지나면 추적을 끝낸다 ──
    if ev.breakout_date is not None:
        if ev.base_low and close < ev.base_low:
            _end(ev, screen_date, "base_broken", vcp_model.INVALIDATED)
            stats["invalidated"] += 1
            return False
        ev.status = vcp_model.BREAKOUT if close > ev.pivot else vcp_model.FAILED_BREAKOUT
        ev.last_seen = screen_date
        days = len(_bars(db, ev.stock_id, ev.breakout_date, screen_date))
        if days >= settings.VCP_FOLLOW_DAYS:
            _end(ev, screen_date, "follow_done")
            stats["resolved"] += 1
            return False
        return True

    # ── 돌파 전 ──
    if ev.base_low and close < ev.base_low:                       # 베이스 저점 아래로 마감 = 베이스가 무너짐
        _end(ev, screen_date, "base_broken", vcp_model.INVALIDATED)
        stats["invalidated"] += 1
        return False
    same = r.vcp2_score is not None and r.vcp2_key == ev.key_date
    # 종가가 피벗 위 = 돌파(거래량 조건 없음). 같은 형성이면 점수 모델이 본 피벗과 돌파 봉을 우선 쓴다 —
    # 새 수축이 생기면 피벗(마지막 수축의 상단)이 전보다 낮아질 수 있어서, 옛 피벗과 비교하면 돌파를 하루 늦게 잡는다.
    if same and r.vcp2_state == vcp_model.BREAKOUT and r.vcp2_age is not None and not unreliable:
        bar = _bar_back(db, ev.stock_id, screen_date, r.vcp2_age)
        if bar:
            _refresh(ev, r, screen_date)                          # 돌파 직전 모습(피벗, 수축, 점수)으로 갱신한 뒤 돌파를 기록
            _record_breakout(db, ev, bar, market, regime_cache, r.vcp2_score)
            stats["breakout"] += 1
            return True
    if ev.pivot and close > ev.pivot:                             # 점수 모델이 형성을 못 봐도 가격으로는 돌파를 잡는다
        hit = _first_close_above(db, ev.stock_id, ev.pivot, ev.last_seen, screen_date) or (screen_date, close, None)
        _record_breakout(db, ev, hit, market, regime_cache, ev.score_last)
        ev.last_seen = screen_date
        stats["breakout"] += 1
        return True
    if unreliable:                                                # 판정 근거(RS, 점수)를 못 믿는 날은 여기서 멈춘다
        return True
    if not trend:                                                 # 추세 게이트 이탈은 grace 동안 흡수
        ev.trend_grace = (ev.trend_grace or 0) + 1
        if ev.trend_grace > settings.VCP_TREND_GRACE_DAYS:
            _end(ev, screen_date, "trend_lost", vcp_model.INVALIDATED)
            stats["invalidated"] += 1
            return False
        return True
    if same:
        if r.vcp2_score < settings.VCP_SCORE_HOLD:
            _end(ev, screen_date, "score_low", vcp_model.INVALIDATED)
            stats["invalidated"] += 1
            return False
        _refresh(ev, r, screen_date)
        stats["updated"] += 1
        return True
    if r.vcp2_key is not None:                                    # 다른 베이스가 보임 = 이 형성은 밀려남
        _end(ev, screen_date, "superseded", vcp_model.INVALIDATED)
        stats["invalidated"] += 1
        return False
    if (screen_date - ev.last_seen).days > settings.VCP_FORMATION_STALE_DAYS:
        _end(ev, screen_date, "stale", vcp_model.INVALIDATED)
        stats["invalidated"] += 1
        return False
    return True


def _maybe_create(db: Session, r, trend: bool, screen_date: date, market: str, regime_cache: dict, stats: dict):
    """후보 기준을 넘는 새 형성을 등록한다. 같은 key의 형성이 이미 있으면(닫힌 것 포함) 만들지 않는다."""
    if not trend or r.vcp2_score is None or r.vcp2_key is None or r.vcp2_score < vcp_model.MIN_SCORE:
        return None
    state = r.vcp2_state
    if state == vcp_model.BREAKOUT:
        if r.vcp2_age is None or r.vcp2_age > 3:                  # 한참 전에 돌파한 것은 새로 올리지 않음
            return None
    elif state not in PRE_STATES:
        return None
    if db.query(VCPFormation.id).filter(VCPFormation.stock_id == r.stock_id,
                                        VCPFormation.key_date == r.vcp2_key).first():
        return None
    base_high, base_low = _base_extremes(db, r.stock_id, r.vcp2_key, screen_date)
    seq = db.query(VCPFormation.id).filter(VCPFormation.stock_id == r.stock_id).count() + 1
    ev = VCPFormation(
        stock_id=r.stock_id, key_date=r.vcp2_key, first_detected=screen_date, last_seen=screen_date,
        status=state if state in PRE_STATES else vcp_model.BREAKOUT, base_seq=seq,
        base_high=base_high, base_low=base_low, pivot=r.vcp2_pivot,
        contractions=len(r.vcp2_depths.split("/")) if r.vcp2_depths else None, depths=r.vcp2_depths,
        score_first=r.vcp2_score, score_peak=r.vcp2_score, score_last=r.vcp2_score,
        grade_last=r.vcp2_grade, parts_last=r.vcp2_parts, trend_grace=0, rs_rank=r.rs_rank, close=r.close,
    )
    db.add(ev)
    if state == vcp_model.BREAKOUT:
        bar = _bar_back(db, r.stock_id, screen_date, r.vcp2_age or 0)
        if bar:
            _record_breakout(db, ev, bar, market, regime_cache, r.vcp2_score)
            stats["breakout"] += 1
    stats["new"] += 1
    return ev


def update_vcp_formations(db: Session, screen_date: date, skip_stock_ids: set | None = None, rows=None) -> dict:
    """오늘 결과로 VCP 형성 레지스트리를 갱신한다. 같은 날 두 번 돌려도 결과가 같다(멱등).

    skip_stock_ids: 새 종가가 없는 시장의 종목 등 이번에 건드리지 않을 종목(구 레지스트리와 같은 규칙).
    rows: 과거 재생용. 주지 않으면 screen_date의 ScreeningResult를 쓴다.
    """
    from app.screener import _trend_ok

    results = rows if rows is not None else db.query(ScreeningResult).filter(
        ScreeningResult.screen_date == screen_date).all()
    skip = set(skip_stock_ids or ())
    open_evs = {e.stock_id: e for e in db.query(VCPFormation)
                .filter(VCPFormation.resolved_date.is_(None)).order_by(VCPFormation.first_detected).all()}
    market_of = dict(db.query(Stock.id, Stock.market).all())
    regime_cache: dict = {}
    stats = {"new": 0, "updated": 0, "breakout": 0, "invalidated": 0, "resolved": 0}

    # RS가 대부분 비어 있는 시장은 그날 수집이 깨진 것이다(2026-08~09에 실제로 있었음). 그런 날 추세 게이트를
    # 그대로 믿으면 열린 형성이 한꺼번에 trend_lost로 닫히므로, 해당 시장은 가격 기준 판정만 하고 신규 등록도 쉰다.
    seen, empty_rs = {}, {}
    for r in results:
        m = market_of.get(r.stock_id) or "US"
        seen[m] = seen.get(m, 0) + 1
        empty_rs[m] = empty_rs.get(m, 0) + (0 if r.rs_rank else 1)
    broken = {m for m, n in seen.items() if n >= 20 and empty_rs[m] / n > RS_BROKEN_SHARE}
    if broken:
        logger.warning("RS가 대부분 비어 있는 시장 %s — 추세·점수 기준 판정을 건너뜀(가격 기준 판정만)", sorted(broken))

    for r in results:
        sid = r.stock_id
        if sid in skip:
            continue
        market = market_of.get(sid) or "US"
        unreliable = market in broken
        ev = open_evs.get(sid)
        if getattr(r, "signal", None) == "DATA":                   # 데이터 이상은 갱신하지 않되 오래 방치된 형성은 닫는다
            if ev and (screen_date - ev.last_seen).days > settings.VCP_FORMATION_STALE_DAYS:
                _end(ev, screen_date, "stale", vcp_model.INVALIDATED)
                stats["invalidated"] += 1
            continue
        trend = _trend_ok(r)
        if ev is not None:
            if _advance(db, ev, r, trend, screen_date, market, regime_cache, stats, unreliable):
                continue
            open_evs.pop(sid, None)
        if unreliable:
            continue
        new = _maybe_create(db, r, trend, screen_date, market, regime_cache, stats)
        if new is not None:
            open_evs[sid] = new

    db.commit()
    logger.info("VCP 형성 추적: 신규 %(new)d, 갱신 %(updated)d, 돌파 %(breakout)d, 무효 %(invalidated)d, 추적 종료 %(resolved)d", stats)
    return stats


def update_formation_outcomes(db: Session, screen_date: date, recompute: bool = False) -> dict:
    """돌파한 형성의 손절·목표 도달 결과를 확정한다. 규칙은 구 레지스트리와 같다(screener.settle_outcomes)."""
    from app.screener import settle_outcomes

    q = db.query(VCPFormation).filter(VCPFormation.breakout_date.isnot(None))
    if recompute:
        for ev in q.all():
            ev.outcome = ev.outcome_date = ev.outcome_pct = None
        db.flush()
    return settle_outcomes(db, screen_date, q.filter(VCPFormation.outcome.is_(None)).all())
