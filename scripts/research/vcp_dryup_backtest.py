# -*- coding: utf-8 -*-
"""VCP 거래량 마름(dry-up) 두 정의 비교 — 전체 과거표본(2021~) 백테스트.

기존 플래그(vcp_volume_dryup): 최근 10일 평균 거래량 < 50일 평균의 85%.
새 정의(미너비니): 마지막 수축 구간의 최저 거래량이 베이스 전체에서 가장 낮다
(app.screener.vcp_last_contraction_volume).

두 가지 질문을 따로 본다.
  1) 돌파냐 실패냐: 피벗 위 종가(돌파) vs base_low를 UNDERCUT만큼 더 깨면(실패). vcp_tightness_backtest와 같은 판정.
  2) 돌파한 것만 놓고 돌파일 종가 기준 20거래일 뒤 수익률·-8% 손절 도달률(종가 기준).
미너비니가 말한 건 돌파 이후의 폭발력에 가까워 2)가 더 맞는 질문일 수 있다.

주의: 과거 구간 표본이라 '검증 완료'가 아니다. 실제 레지스트리(vcp_events)에 쌓이는 결과와 방향이 같은지 교차확인 용도.

선행: python scripts/research/pull_history.py
사용: python scripts/research/vcp_dryup_backtest.py
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.screener import detect_vcp, vcp_last_contraction_volume  # noqa: E402
from config import settings  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "prices.sqlite")
MAX_WATCH_DAYS = 60
UNDERCUT = settings.VCP_RESET_UNDERCUT
STOP = -8.0


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
    forming = {}
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
            piv, base_low = v.get("pivot"), v.get("base_low")
            if not (v["detected"] and piv and base_low and float(s.iloc[-1]) < piv):
                continue
            dv = dryup_mat[tk].iloc[i - 1]
            nv = vcp_last_contraction_volume(s, vol[tk].iloc[:i])
            forming[tk] = {
                "piv": piv, "base_low": base_low, "start": i, "regime": reg,
                "old_dry": bool(pd.notna(dv) and dv < 0.85),
                "lowest": None if nv is None else nv["lowest_in_last"],
                "vs_prior": None if nv is None else nv["last_vs_prior"],
                "vs_base": None if nv is None else nv["last_vs_base"],
            }

        for tk in list(forming):
            f = forming[tk]
            cn = close[tk].iloc[i]
            if pd.isna(cn):
                continue
            age = i - f["start"]
            rec = {"ticker": tk, "regime": f["regime"], "old_dry": f["old_dry"], "lowest": f["lowest"],
                   "vs_prior": f["vs_prior"], "vs_base": f["vs_base"]}
            if cn >= f["piv"]:
                col = close[tk]
                w = col.iloc[i:i + 21]
                r20 = (col.iloc[i + 20] / cn - 1) * 100 if i + 20 < len(col) and pd.notna(col.iloc[i + 20]) else None
                stopped = bool((w / cn - 1).min() * 100 <= STOP) if len(w) > 1 else None
                rows.append({**rec, "outcome": "breakout", "r20": r20, "stopped": stopped})
                del forming[tk]
            elif cn <= f["base_low"] * (1 - UNDERCUT):
                rows.append({**rec, "outcome": "fail", "r20": None, "stopped": None})
                del forming[tk]
            elif age >= MAX_WATCH_DAYS:
                del forming[tk]

    B = pd.DataFrame(rows)
    B["y"] = (B.outcome == "breakout").astype(int)
    print(f"\n표본 {len(B)}건 (돌파 {B.y.sum()} / 실패 {(1 - B.y).sum()}), 새 정의 계산 가능 {B.lowest.notna().sum()}건\n")

    def auc(y, s):
        d = pd.DataFrame({"y": y, "s": s}).dropna()
        pos, neg = (d.y == 1).sum(), (d.y == 0).sum()
        if not pos or not neg: return float("nan")
        ranks = d.s.rank()
        return (ranks[d.y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)

    N = B[B.lowest.notna()].copy()
    N["lowest"] = N.lowest.astype(bool)

    print("1) 돌파 vs 실패 (돌파율)")
    for name, col in (("기존 플래그(10일/50일<0.85)", "old_dry"), ("새 정의(마지막 수축 최저)", "lowest")):
        g = N.groupby(col).y.agg(["count", "mean"])
        t, f_ = (g.loc[True] if True in g.index else None), (g.loc[False] if False in g.index else None)
        print(f"  {name}: 있음 n={int(t['count'])} 돌파율 {t['mean']*100:.1f}%  /  없음 n={int(f_['count'])} 돌파율 {f_['mean']*100:.1f}%")
    print(f"  AUC 기존 플래그(있으면 가점)   : {auc(N.y, N.old_dry.astype(int)):.3f}")
    print(f"  AUC 새 정의 bool(있으면 가점)  : {auc(N.y, N.lowest.astype(int)):.3f}")
    print(f"  AUC last_vs_prior(작을수록 가점): {auc(N.y, -N.vs_prior):.3f}")
    print(f"  AUC last_vs_base (작을수록 가점): {auc(N.y, -N.vs_base):.3f}")

    print("\n2) 돌파한 것만: 돌파일 종가 기준 20거래일 뒤")
    Bk = N[N.outcome == "breakout"]
    def stat(sub):
        r = sub.r20.dropna(); st = sub.stopped.dropna()
        if len(r) < 20:
            return f"n={len(sub)} (표본부족)"
        return f"n={len(sub):4d}  중앙 {r.median():+5.2f}%  평균 {r.mean():+5.2f}%  승률 {(r>0).mean()*100:3.0f}%  -8%손절도달 {st.mean()*100:3.0f}%"
    for name, col in (("기존 플래그", "old_dry"), ("새 정의", "lowest")):
        print(f"  {name} 있음 : {stat(Bk[Bk[col] == True])}")
        print(f"  {name} 없음 : {stat(Bk[Bk[col] == False])}")
    q = pd.qcut(Bk.vs_base, 3, labels=["가장 조용", "중간", "덜 조용"], duplicates="drop")
    print("  last_vs_base 3분위:")
    for lab in q.cat.categories:
        print(f"    {lab:6s}: {stat(Bk[q == lab])}")

    print("\n3) 국면별 새 정의 돌파율")
    for reg in ("BULL", "NEUTRAL", "BEAR"):
        sub = N[N.regime == reg]
        g = sub.groupby("lowest").y.agg(["count", "mean"])
        if True in g.index and False in g.index:
            print(f"  {reg}: 있음 n={int(g.loc[True,'count'])} {g.loc[True,'mean']*100:.1f}% / 없음 n={int(g.loc[False,'count'])} {g.loc[False,'mean']*100:.1f}%")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vcp_dryup_backtest.csv")
    B.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
