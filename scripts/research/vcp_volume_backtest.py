# -*- coding: utf-8 -*-
"""VCP 거래량 판단 백테스트 (강세장 한정, in-sample).

"강세장에서 거래량을 어떻게 볼까"를 세분화 검증한다. 앱의 실제 detect_vcp + 추세게이트를
시점복원으로 재생하고, BULL 국면 + 피벗+5% 이내 진입만 뽑아 (A)돌파 당일 거래량배수
(B)돌파 전 베이스 U/D 매집 (C)베이스 dry-up (D)조합 을 구간별로 비교한다.

선행: python scripts/research/pull_history.py   (→ scripts/research/data/prices.sqlite)
사용: python scripts/research/vcp_volume_backtest.py

주의: 이건 과거표본(in-sample) 분석이다. HANDOFF.md의 'VCP 품질점수 v2'는 여기서 나온
휴리스틱이라, 실제 적용 전에는 레지스트리(vcp_events)에 쌓인 실제 결과로 표본외 검증할 것.
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.screener import detect_vcp  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "prices.sqlite")


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
    ret52 = closef / closef.shift(252) - 1
    above200 = closef > ma200; above50 = closef > ma50
    volx_mat = volf / vol50
    chg = closef.diff()
    ud_mat = volf.where(chg > 0, 0.0).rolling(50).sum() / volf.where(chg < 0, 0.0).rolling(50).sum().replace(0, np.nan)
    dryup_mat = volf.rolling(10).mean() / vol50

    def fwd_ret(tk, d, n):
        col = close[tk].dropna(); idx = col.index.searchsorted(d)
        if idx >= len(col) or col.index[idx] != d or idx + n >= len(col):
            return None
        return (col.iloc[idx + n] / col.iloc[idx] - 1) * 100

    def uni_median(n, i):
        return None if i + n >= len(dates) else float(((close.iloc[i + n] / close.iloc[i] - 1) * 100).median())

    def regime_of(i):
        r200, r50 = above200.iloc[i], above50.iloc[i]; nn = r200.notna().sum()
        if nn == 0: return "UNKNOWN"
        p200 = r200.sum() / nn * 100; p50 = r50.sum() / nn * 100
        if p200 >= 60 and p50 >= 50: return "BULL"
        if p200 < 40: return "BEAR"
        return "NEUTRAL"

    rows = []; forming = {}
    for i in range(255, len(dates) - 1):
        reg = regime_of(i - 1)
        c_prev, c_now = closef.iloc[i - 1], close.iloc[i]
        rs_rank = ret52.iloc[i - 1].groupby(mkt_map.reindex(ret52.columns)).rank(pct=True) * 100
        gate = ((c_prev > ma150.iloc[i-1]) & (c_prev > ma200.iloc[i-1]) & (ma150.iloc[i-1] > ma200.iloc[i-1])
                & (ma200.iloc[i-1] > ma200.iloc[i-22]) & (c_prev > ma50.iloc[i-1]) & (rs_rank >= 70))
        for tk in gate.index[gate.fillna(False)]:
            s = close[tk].iloc[:i].dropna()
            if len(s) < 200: continue
            v = detect_vcp(s); piv = v.get("pivot")
            if v["detected"] and piv and float(s.iloc[-1]) < piv:
                forming[tk] = (piv, i)
        for tk in list(forming):
            piv, ti = forming[tk]
            if i - ti > 15: del forming[tk]; continue
            cp, cn = c_prev.get(tk), c_now.get(tk)
            if cp is None or cn is None or pd.isna(cp) or pd.isna(cn): continue
            if not (cp < piv <= cn): continue
            del forming[tk]; d_now = dates[i]
            col = close[tk].dropna(); j = col.index.get_loc(d_now); w = col.iloc[j:j+21]
            rows.append({
                "ticker": tk, "date": str(d_now.date()), "regime": reg, "ext": (cn / piv - 1) * 100,
                "volx_bo": float(volx_mat[tk].iloc[i]) if pd.notna(volx_mat[tk].iloc[i]) else None,
                "ud_pre": float(ud_mat[tk].iloc[i-1]) if pd.notna(ud_mat[tk].iloc[i-1]) else None,
                "dryup_pre": float(dryup_mat[tk].iloc[i-1]) if pd.notna(dryup_mat[tk].iloc[i-1]) else None,
                "r20": fwd_ret(tk, d_now, 20), "b20": uni_median(20, i),
                "stopped": bool((w / cn - 1).min() * 100 <= -8) if len(w) > 1 else None,
            })

    B = pd.DataFrame(rows)
    U = B[(B.regime == "BULL") & (B.ext <= 5)].copy()
    print(f"\nBULL & 피벗+5%이내 돌파: {len(U)}건 (전체 {len(B)}건)\n", flush=True)

    def stat(sub):
        r = sub["r20"].dropna()
        if len(r) < 8: return f"n={len(sub):4d} (표본부족)"
        ex = (sub["r20"] - sub["b20"]).dropna(); st = sub["stopped"].dropna()
        return (f"n={len(sub):4d}  +20일중앙 {r.median():+5.2f}%  승률 {(r>0).mean()*100:3.0f}%  "
                f"초과 {ex.median():+5.2f}%p  손절 {st.mean()*100:3.0f}%")

    def bucketize(col, edges, labels):
        print(f"── {col} ──")
        for lo, hi, lab in zip(edges[:-1], edges[1:], labels):
            print(f"  {lab:16s} {stat(U[(U[col] >= lo) & (U[col] < hi)])}")
        print()

    print("A) 돌파 당일 거래량배수"); bucketize("volx_bo", [0,0.8,1.0,1.4,2.0,99],
        ["<0.8x 한산","0.8~1.0x","1.0~1.4x","1.4~2.0x 대량",">=2.0x 폭증"])
    print("B) 베이스 U/D 매집비율"); bucketize("ud_pre", [0,0.8,1.0,1.25,1.5,99],
        ["<0.8 분산","0.8~1.0","1.0~1.25 매집","1.25~1.5",">=1.5 강매집"])
    print("C) 베이스 dry-up"); bucketize("dryup_pre", [0,0.6,0.85,1.1,99],
        ["<=0.6 크게마름","0.6~0.85 마름","0.85~1.1 보통",">1.1 늘어남"])


if __name__ == "__main__":
    main()
