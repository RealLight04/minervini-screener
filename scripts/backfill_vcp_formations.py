# -*- coding: utf-8 -*-
"""vcp_formations 초기 채우기: 최근 스크리닝 날짜들을 하루씩 재생해 형성 이력을 만든다(Owner 전용, 한 번만).

왜 필요한가
  새 레지스트리는 오늘부터 쌓이므로 그냥 두면 first_detected가 전부 "오늘"이고 최근 돌파 이력이 비어 있다.

왜 저장된 스크리닝 값을 그대로 쓰지 않나
  2026-08-04~09-01에는 가격 수집이 멈춰 screening_results의 종가가 한 달 가까이 얼어 있었고(일봉은 나중에 복구됨),
  RS는 8월 중순~9월 말 사이 여러 날(08-08~09-01, 09-11, 09-22~23) 대부분 0으로 저장돼 있다. 그 값을 쓰면 추세 게이트가
  엉뚱하게 전멸한다. 그래서 날짜 d마다 복구된 일봉에서 아래를 직접 다시 계산해 쓴다.
    종가            d일 이전 마지막 봉의 종가
    추세 조건 8개   screen_stock과 같은 식(종가/이동평균/52주 고저)
    RS              52주 전 종가 대비 수익률의 같은 시장 안 백분위(매일 배치의 _calc_rs_scores와 같은 방식, 'd 기준'으로)
    VCP 점수        app.screener.vcp2_fields(매일 배치와 같은 함수)
  재계산이 맞는지는 마지막 날짜와 건강한 날짜의 저장값과 대조해서 출력한다.

재생 규칙
  - 날짜는 screening_results에 있는 최근 N개 스크리닝 날짜(오름차순). 그 종목의 새 일봉이 직전 재생일과 같으면
    (주말, 휴장, 시장 시차) 건너뜀 — 매일 배치가 새 종가 없는 시장을 건너뛰는 것과 같다.
  - 기본은 vcp_formations가 비어 있을 때만 실행. 다시 하려면 --reset(새 테이블이라 잃는 기록 없음).

사용(프로젝트 루트에서, DATABASE_URL은 CWD 상대경로라 다른 곳에서 돌리면 빈 DB가 생긴다):
  PYTHONUTF8=1 ENABLE_SCHEDULER=false venv/Scripts/python.exe scripts/backfill_vcp_formations.py [--days 40] [--reset]
"""
import argparse
import os
import sys
import time
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import DailyPrice, ScreeningResult, Stock, VCPFormation  # noqa: E402
from app.screener import vcp2_fields  # noqa: E402
from app.vcp_tracker import update_formation_outcomes, update_vcp_formations  # noqa: E402
from config import settings  # noqa: E402

VCP2_KEYS = ("vcp2_score", "vcp2_grade", "vcp2_state", "vcp2_pivot", "vcp2_depths", "vcp2_key",
             "vcp2_parts", "vcp2_bvr", "vcp2_age")
COND_KEYS = ("cond_price_above_ma150", "cond_price_above_ma200", "cond_ma150_above_ma200", "cond_ma50_above_ma150_200",
             "cond_ma200_uptrend", "cond_price_above_ma50", "cond_above_52w_low_30pct", "cond_within_52w_high_25pct",
             "cond_rs_rank")


def prepare(frames: dict) -> dict:
    """종목별로 이동평균과 52주 고저를 한 번만 계산해 둔다(롤링은 과거 값에만 의존하므로 날짜별로 자르지 않아도 같다)."""
    out = {}
    for sid, df in frames.items():
        c = df["close"]
        out[sid] = {"df": df, "dates": df.index.to_numpy(), "close": c.to_numpy(),
                    "ma50": c.rolling(50).mean().to_numpy(), "ma150": c.rolling(150).mean().to_numpy(),
                    "ma200": c.rolling(200).mean().to_numpy(), "hi": c.rolling(252, min_periods=1).max().to_numpy(),
                    "lo": c.rolling(252, min_periods=1).min().to_numpy()}
    return out


def position(p: dict, d) -> int:
    """d일 이전(포함) 마지막 봉의 위치. 없으면 -1."""
    return int(np.searchsorted(p["dates"], d, side="right")) - 1


def trend_conds(p: dict, i: int, rs: float) -> dict | None:
    """screen_stock과 같은 식의 추세 조건. 200봉 미만이면 None(매일 배치도 이 종목은 판정하지 않음)."""
    if i < 199:
        return None
    close, ma50, ma150, ma200 = p["close"][i], p["ma50"][i], p["ma150"][i], p["ma200"][i]
    ma200_prev = p["ma200"][i - 21] if i >= 221 else None      # screen_stock: rolling(200).iloc[-22]
    return {
        "cond_price_above_ma150": bool(close > ma150), "cond_price_above_ma200": bool(close > ma200),
        "cond_ma150_above_ma200": bool(ma150 > ma200), "cond_ma50_above_ma150_200": bool(ma50 > ma150 and ma50 > ma200),
        "cond_ma200_uptrend": bool(ma200_prev is not None and ma200 > ma200_prev), "cond_price_above_ma50": bool(close > ma50),
        "cond_above_52w_low_30pct": bool(close >= p["lo"][i] * 1.30), "cond_within_52w_high_25pct": bool(close >= p["hi"][i] * 0.75),
        "cond_rs_rank": bool(rs >= 100 - settings.RS_TOP_PERCENTILE),
    }


def rs_asof(P: dict, market_of: dict, d) -> dict:
    """d일 기준 RS(0~100): 52주 전 종가(±10일 안에서 가장 가까운 봉) 대비 수익률의 같은 시장 안 백분위."""
    target = np.datetime64(d - timedelta(weeks=52))
    lo, hi = np.datetime64(d - timedelta(weeks=52, days=10)), np.datetime64(d - timedelta(weeks=52) + timedelta(days=10))
    perf: dict = {}
    for sid, p in P.items():
        i = position(p, d)
        if i < 0 or (d - p["dates"][i]).days > 7:
            continue
        dates = p["dates"]
        a, b = int(np.searchsorted(dates, lo, side="left")), int(np.searchsorted(dates, hi, side="right"))
        if a >= b:
            continue
        j = a + int(np.argmin([abs((dates[k] - target.astype(object)).days) for k in range(a, b)]))
        if p["close"][j] > 0:
            perf.setdefault(market_of.get(sid) or "US", {})[sid] = (p["close"][i] / p["close"][j] - 1) * 100
    out: dict = {}
    for dct in perf.values():
        out.update((pd.Series(dct).rank(pct=True) * 100).to_dict())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=40, help="재생할 최근 스크리닝 날짜 수")
    ap.add_argument("--reset", action="store_true", help="vcp_formations를 비우고 다시 채움")
    args = ap.parse_args()

    init_db()   # 새 테이블 생성(create_all)
    db = SessionLocal()
    try:
        existing = db.query(VCPFormation).count()
        if existing and not args.reset:
            sys.exit(f"vcp_formations에 이미 {existing}건이 있습니다. 다시 채우려면 --reset")
        if args.reset:
            db.query(VCPFormation).delete()
            db.commit()

        dates = [d for (d,) in db.query(ScreeningResult.screen_date).distinct().order_by(ScreeningResult.screen_date.desc())
                 .limit(args.days).all()][::-1]
        if not dates:
            sys.exit("재생할 스크리닝 결과가 없습니다")
        print(f"재생 {len(dates)}일: {dates[0]} ~ {dates[-1]}", flush=True)

        t0 = time.time()
        px = pd.read_sql(
            db.query(DailyPrice.stock_id, DailyPrice.date, DailyPrice.open, DailyPrice.high, DailyPrice.low,
                     DailyPrice.close, DailyPrice.volume)
            .filter(DailyPrice.date >= dates[0] - timedelta(days=420)).statement, db.bind)
        frames = {sid: g.set_index("date")[["open", "high", "low", "close", "volume"]].astype(float)
                  .dropna(subset=["high", "low", "close"]).sort_index() for sid, g in px.groupby("stock_id")}
        P = prepare(frames)
        print(f"일봉 {len(px):,}행 적재, {len(frames)}종목 ({time.time() - t0:.0f}s)", flush=True)

        market_of = dict(db.query(Stock.id, Stock.market).all())
        prev_last: dict = {}
        check = {"cond": [0, 0], "rs": [0, 0], "vcp2": [0, 0]}   # [비교한 값, 불일치]
        check_dates = {dates[-1], dates[-6] if len(dates) > 6 else dates[-1]}
        for n, d in enumerate(dates):
            results = db.query(ScreeningResult).filter(ScreeningResult.screen_date == d).all()
            rs_all = rs_asof(P, market_of, d)
            rows, skip = [], set()
            for r in results:
                p = P.get(r.stock_id)
                if p is None:
                    continue
                i = position(p, d)
                if i < 0:
                    continue
                last = p["dates"][i]
                if prev_last.get(r.stock_id) == last:
                    skip.add(r.stock_id)             # 새 일봉이 없으면 건드리지 않는다
                prev_last[r.stock_id] = last
                rs = rs_all.get(r.stock_id, 0.0)
                conds = trend_conds(p, i, rs)
                fields = dict.fromkeys(VCP2_KEYS)
                if r.signal != "DATA" and conds is not None:
                    df = p["df"].iloc[: i + 1]
                    df = df[df.index >= d - timedelta(days=370)]
                    fields.update(vcp2_fields(df, [conds[k] for k in COND_KEYS[:8]], rs))
                if d in check_dates and conds is not None and r.signal != "DATA" and r.cond_price_above_ma150 is not None:
                    for k in COND_KEYS[:8]:           # 저장값(건강한 날짜)과 재계산 대조
                        check["cond"][0] += 1
                        check["cond"][1] += int(bool(getattr(r, k)) != conds[k])
                    check["rs"][0] += 1
                    check["rs"][1] += int(abs((r.rs_rank or 0) - rs) > 3)
                    if d == dates[-1]:
                        for k in ("vcp2_score", "vcp2_state", "vcp2_pivot", "vcp2_key", "vcp2_depths"):
                            a, b = getattr(r, k), fields[k]
                            check["vcp2"][0] += 1
                            check["vcp2"][1] += int(not (a == b or (isinstance(a, float) and isinstance(b, float) and abs(a - b) < 0.6)))
                rows.append(SimpleNamespace(
                    stock_id=r.stock_id, close=float(p["close"][i]), signal=r.signal, rs_rank=rs,
                    **{k: (conds[k] if conds else None) for k in COND_KEYS}, **fields))
            stats = update_vcp_formations(db, d, skip_stock_ids=skip, rows=rows)
            print(f"  {n + 1:>2}/{len(dates)} {d}  행 {len(rows)}, 건너뜀 {len(skip)}  {stats}", flush=True)

        out = update_formation_outcomes(db, dates[-1])
        print(f"결과 확정 {out}")
        for k, (tot, bad) in check.items():
            print(f"저장값 대조[{k}] ({', '.join(str(x) for x in sorted(check_dates))}): {tot}개 중 불일치 {bad}")
        by = db.query(VCPFormation.status, VCPFormation.resolved_date.is_(None)).all()
        print("상태(열림 여부):", dict(Counter((s, "열림" if o else "종료") for s, o in by)))
        print(f"총 {db.query(VCPFormation).count()}건, 소요 {time.time() - t0:.0f}s")
    finally:
        db.close()


if __name__ == "__main__":
    main()
