# -*- coding: utf-8 -*-
"""VCP 마지막 수축폭(타이트함)과 돌파/실패 결과의 관계 — 전체 과거표본(2021~) 백테스트.

라이브 레지스트리(vcp_events)는 2026-07 이후 3개월, 88건뿐이고 거의 전부 강세장
한 시기에 몰려있어 last_contraction 가중치(0.25) 판단엔 표본이 작았다(z=1.90, 경계선).
이 스크립트는 같은 질문을 scripts/research/data/prices.sqlite(2021~2026, 822종목,
여러 국면 포함)로 훨씬 큰 표본에서 재현한다.

방법: 앱의 실제 detect_vcp + 추세게이트를 시점복원으로 재생. 피벗을 가진 VCP를
감지하면 이후 매일 추적해 (a) 종가가 피벗 이상 → 돌파, (b) 종가가 base_low를
VCP_RESET_UNDERCUT만큼 더 깨면 → 실패, (c) 60거래일 안에 둘 다 없으면 → 미결판(제외)
으로 분류한다. vcp_events의 broke_out/failed 판정 로직과 같은 개념.

주의: 이것도 특정 과거 구간 표본이라 '검증 완료'는 아니다. 레지스트리의 실제 라이브
결과와 방향이 같은지 보는 교차검증 정도로 쓸 것.

선행: python scripts/research/pull_history.py
사용: python scripts/research/vcp_tightness_backtest.py
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.screener import detect_vcp  # noqa: E402
from config import settings  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "prices.sqlite")
MAX_WATCH_DAYS = 60
UNDERCUT = settings.VCP_RESET_UNDERCUT


def main():
    if not os.path.exists(DB):
        print(f"가격 DB 없음: {DB}\n먼저 실행: python scripts/research/pull_history.py")
        return
    con = sqlite3.connect(DB)
    px = pd.read_sql("SELECT ticker, market, date, close, volume FROM prices", con, parse_dates=["date"])
    con.close()

    close = px.pivot(index="date", columns="ticker", values="close").sort_index()
    vol = px.pivot(index="date", columns="ticker", values="volume").sort_index()
    mkt_map = px.drop_duplicates("ticker").set_index("ticker")["market"]
    dates = close.index
    print(f"데이터: {len(dates)}거래일 {dates[0].date()}~{dates[-1].date()}, {close.shape[1]}종목", flush=True)

    closef = close.ffill(); volf = vol.ffill()
    ma50 = closef.rolling(50).mean(); ma150 = closef.rolling(150).mean(); ma200 = closef.rolling(200).mean()
    vol50 = volf.rolling(50).mean()
    dryup_mat = volf.rolling(10).mean() / vol50
    ret52 = closef / closef.shift(252) - 1
    above200 = closef > ma200; above50 = closef > ma50

    def regime_of(i):
        r200, r50 = above200.iloc[i], above50.iloc[i]; nn = r200.notna().sum()
        if nn == 0: return "UNKNOWN"
        p200 = r200.sum() / nn * 100; p50 = r50.sum() / nn * 100
        if p200 >= 60 and p50 >= 50: return "BULL"
        if p200 < 40: return "BEAR"
        return "NEUTRAL"

    rows = []
    forming = {}  # ticker -> (pivot, base_low, last_contraction, start_i, regime)
    for i in range(255, len(dates)):
        reg = regime_of(i - 1)
        c_prev = closef.iloc[i - 1]
        rs_rank = ret52.iloc[i - 1].groupby(mkt_map.reindex(ret52.columns)).rank(pct=True) * 100
        gate = ((c_prev > ma150.iloc[i-1]) & (c_prev > ma200.iloc[i-1]) & (ma150.iloc[i-1] > ma200.iloc[i-1])
                & (ma200.iloc[i-1] > ma200.iloc[i-22]) & (c_prev > ma50.iloc[i-1]) & (rs_rank >= 70))
        for tk in gate.index[gate.fillna(False)]:
            if tk in forming:
                continue
            s = close[tk].iloc[:i].dropna()
            if len(s) < 200:
                continue
            v = detect_vcp(s)
            piv, base_low, last = v.get("pivot"), v.get("base_low"), v.get("last_pct")
            if v["detected"] and piv and base_low and last is not None and float(s.iloc[-1]) < piv:
                dv = dryup_mat[tk].iloc[i-1] if tk in dryup_mat.columns else None
                dryup = bool(pd.notna(dv) and dv < 0.85)
                forming[tk] = (piv, base_low, last, i, reg, dryup)

        for tk in list(forming):
            piv, base_low, last, start_i, reg0, dryup0 = forming[tk]
            cn = close[tk].iloc[i] if tk in close.columns else None
            if cn is None or pd.isna(cn):
                continue
            age = i - start_i
            if cn >= piv:
                rows.append({"ticker": tk, "regime": reg0, "last_contraction": last, "volume_dryup": dryup0, "outcome": "breakout", "age": age})
                del forming[tk]
            elif cn <= base_low * (1 - UNDERCUT):
                rows.append({"ticker": tk, "regime": reg0, "last_contraction": last, "volume_dryup": dryup0, "outcome": "fail", "age": age})
                del forming[tk]
            elif age >= MAX_WATCH_DAYS:
                del forming[tk]  # 미결판 — 제외

    B = pd.DataFrame(rows)
    print(f"\n표본: {len(B)}건 (돌파 {(B.outcome=='breakout').sum()} / 실패 {(B.outcome=='fail').sum()})\n", flush=True)

    def auc(y, s):
        d = pd.DataFrame({"y": y, "s": s}).dropna()
        pos, neg = (d.y == 1).sum(), (d.y == 0).sum()
        if not pos or not neg: return float("nan")
        ranks = d.s.rank()
        return (ranks[d.y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)

    B["y"] = (B.outcome == "breakout").astype(int)
    print(f"전체 AUC (last_contraction, 작을수록 타이트) : {auc(B.y, -B.last_contraction):.3f}  (0.5=무작위, 가설대로면 >0.5)")
    print(f"  (last_contraction 그대로 랭킹: {auc(B.y, B.last_contraction):.3f} — 공식이 거는 방향)")

    print("\n국면별:")
    for reg in ("BULL", "NEUTRAL", "BEAR"):
        sub = B[B.regime == reg]
        if len(sub) < 20:
            print(f"  {reg}: n={len(sub)} (표본부족)")
            continue
        print(f"  {reg}: n={len(sub)}  AUC(공식 방향)={auc(sub.y, sub.last_contraction):.3f}  "
              f"돌파평균={sub[sub.y==1].last_contraction.mean():.1f}%  실패평균={sub[sub.y==0].last_contraction.mean():.1f}%")

    print()
    dr = B.groupby("volume_dryup").y.agg(["count", "mean"]).rename(columns={"mean": "돌파율"})
    print("거래량 마름 여부별 돌파율:")
    print(dr)
    print(f"AUC(volume_dryup, 공식 방향=있으면 가점) : {auc(B.y, B.volume_dryup.astype(int)):.3f}")

    print("\n타이트함 구간별 돌파율:")
    bins = pd.cut(B.last_contraction, [0, 5, 8, 12, 100], include_lowest=True)
    print(B.groupby(bins, observed=True).y.agg(["count", "mean"]).rename(columns={"mean": "돌파율"}))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vcp_tightness_backtest.csv")
    B.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
