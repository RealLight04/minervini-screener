# -*- coding: utf-8 -*-
"""청산 관리 방식 비교(A)와 진입 타이밍 요인(B). vcp_expectancy_backtest의 후속.

앞선 결과: 제품 STRONG_BUY 상태 진입은 단순 +20% 전량매도 규칙에서 평균 +0.14R이었고,
피벗 조건 없이 트렌드 템플릿만 통과한 종목(대조군)을 사면 +0.19R로 오히려 더 높았다.
여기서는 (A) 제품이 실제로 안내하는 청산 관리를 넣으면 달라지는지,
(B) 대조군 안에서 어떤 진입 요인이 성과를 가르는지 본다.

진입은 신호 다음 날 시가, 리스크는 진입가 -8%(R=8%). 일중 고저 사용, 갭은 시가 체결, 같은 날
손절과 목표가 겹치면 손절 우선. 수수료·슬리피지 없음. 실적 게이트 없음(과거 데이터 없음).

청산 변형
  M0  단순: 손절 -8% 또는 목표 +2.5R 전량, 60거래일 뒤 종가 평가 (앞선 백테스트와 같음)
  M1a 제품 관리: 손절 -8%. 목표(2.5R)에서 절반 익절, 나머지는 본전 손절로 올리고,
      종가가 50일선 아래로 마감하면 다음 날 시가에 정리. 90거래일 뒤 종가 평가
  M1b M1a와 같지만 50일선 이탈 정리를 처음부터 전체 물량에 적용(제품 문구 그대로)
  M2  추세추종: 손절 -8%만. 목표 없이 종가가 50일선 아래로 마감하면 다음 날 시가에 정리

모든 변형을 90거래일 창이 다 지난 진입에만 적용해 같은 표본으로 비교한다(최근 약 4.5개월 제외).

(B) 채택 기준(결과 전에 정함): 해당 구간이 전체 대비 20개 분기 중 70% 이상에서 앞서고,
평균 차이가 +0.05R 이상, 표본 150 이상.

선행: python scripts/research/pull_history.py
사용: python scripts/research/vcp_exit_timing_backtest.py
"""
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.screener import detect_pivot, detect_vcp  # noqa: E402
from config import settings  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "prices.sqlite")
OUT = os.path.join(HERE, "data", "vcp_exit_timing_backtest.csv")
MAX_HOLD0, MAX_HOLD_M, R_TARGET, MAX_DAYS_ABOVE = 60, 90, 2.5, 10
MIN_N = 150
MODES = ["M0", "M1a", "M1b", "M2"]


def main():
    t0 = time.time()
    con = sqlite3.connect(DB)
    px = pd.read_sql("SELECT ticker, market, date, open, high, low, close, volume FROM prices", con, parse_dates=["date"])
    con.close()

    def mat(col):
        return px.pivot(index="date", columns="ticker", values=col).sort_index()

    C, O, H, L, V = mat("close"), mat("open"), mat("high"), mat("low"), mat("volume")
    tickers = list(C.columns)
    dates = C.index
    T = len(dates)
    mkt = px.drop_duplicates("ticker").set_index("ticker")["market"].reindex(tickers)
    Oa, Ha, La, Ca = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy()

    cf, vf = C.ffill(), V.ffill()
    ma50, ma150, ma200 = (cf.rolling(n).mean() for n in (50, 150, 200))
    M50 = ma50.to_numpy()
    hi252, lo252 = cf.rolling(252).max(), cf.rolling(252).min()
    HI252 = hi252.to_numpy()
    ret52 = cf / cf.shift(252) - 1
    vol50 = vf.rolling(50).mean()
    rs = ret52.copy()
    regime = pd.DataFrame(index=dates, columns=sorted(mkt.dropna().unique()), dtype=object)
    P200, P50 = {}, {}
    for m in regime.columns:
        cols = [t for t in tickers if mkt[t] == m]
        rs[cols] = ret52[cols].rank(axis=1, pct=True) * 100
        p200 = (cf[cols] > ma200[cols]).where(ma200[cols].notna()).mean(axis=1) * 100
        p50 = (cf[cols] > ma50[cols]).where(ma50[cols].notna()).mean(axis=1) * 100
        P200[m], P50[m] = p200.to_numpy(), p50.to_numpy()
        regime[m] = np.where((p200 >= 60) & (p50 >= 50), "BULL", np.where(p200 < 40, "BEAR", "NEUTRAL"))
    RS = rs.to_numpy()
    minp = pd.Series([settings.MIN_PRICE if mkt[t] == "US" else settings.MIN_PRICE_KR for t in tickers], index=tickers)
    gate = ((cf > ma150) & (cf > ma200) & (ma150 > ma200) & (ma50 > ma150) & (ma50 > ma200)
            & (ma200 > ma200.shift(21)) & (cf > ma50) & (cf >= lo252 * 1.30) & (cf >= hi252 * 0.75)
            & (rs >= 100 - settings.RS_TOP_PERCENTILE) & cf.ge(minp, axis=1) & (vol50 >= settings.MIN_VOLUME))
    gate = gate.fillna(False).to_numpy()
    print(f"{T}거래일 {dates[0].date()}~{dates[-1].date()}, {len(tickers)}종목, 준비 {time.time() - t0:.0f}s", flush=True)

    def sim0(k, j, entry):
        risk = entry * 0.08
        stop, target = entry - risk, entry + R_TARGET * risk
        last = None
        for d in range(j, j + MAX_HOLD0):
            o, hi, lo, cl = Oa[d, k], Ha[d, k], La[d, k], Ca[d, k]
            if d > j and not np.isnan(o) and o <= stop:
                return (o - entry) / risk, "stop", d - j + 1
            if not np.isnan(lo) and lo <= stop:
                return -1.0, "stop", d - j + 1
            if d > j and not np.isnan(o) and o >= target:
                return (o - entry) / risk, "target", d - j + 1
            if not np.isnan(hi) and hi >= target:
                return R_TARGET, "target", d - j + 1
            if not np.isnan(cl):
                last = cl
        return None if last is None else ((last - entry) / risk, "timeout", MAX_HOLD0)

    def sim_m(k, j, entry, mode):
        risk = entry * 0.08
        stop, target = entry - risk, entry + R_TARGET * risk
        use_target = mode == "M1a" or mode == "M1b"
        trail_all = mode in ("M1b", "M2")
        half, realized, rem_stop, exit_next, last = False, 0.0, stop, False, None
        end = j + MAX_HOLD_M
        for d in range(j, end):
            o, hi, lo, cl = Oa[d, k], Ha[d, k], La[d, k], Ca[d, k]
            w = 0.5 if half else 1.0
            if exit_next and not np.isnan(o):
                return realized + w * (o - entry) / risk, "trail", d - j
            if d > j and not np.isnan(o) and o <= rem_stop:
                return realized + w * (o - entry) / risk, ("be" if half else "stop"), d - j + 1
            if not np.isnan(lo) and lo <= rem_stop:
                return realized + w * (rem_stop - entry) / risk, ("be" if half else "stop"), d - j + 1
            if use_target and not half:
                gap_up = d > j and not np.isnan(o) and o >= target
                if gap_up or (not np.isnan(hi) and hi >= target):
                    fill = o if gap_up else target
                    realized, half, rem_stop = 0.5 * (fill - entry) / risk, True, entry
            if not np.isnan(cl):
                last = cl
                m = M50[d, k]
                if (trail_all or half) and not np.isnan(m) and cl < m:
                    exit_next = True
        if last is None:
            return None
        w = 0.5 if half else 1.0
        return realized + w * (last - entry) / risk, "timeout", MAX_HOLD_M

    rows, seen = [], {}
    for i in range(255, T - 1 - MAX_HOLD_M):
        cand = np.flatnonzero(gate[i])
        if not len(cand):
            continue
        j = i + 1
        for k in cand:
            tk = tickers[k]
            if np.isnan(Ca[i, k]) or np.isnan(Oa[j, k]):
                continue
            s = C[tk].iloc[:i + 1].dropna()
            if len(s) < 200:
                continue
            close = float(s.iloc[-1])
            kinds = []
            if i % 5 == 0:
                kinds.append(("base", "base"))
            v = detect_vcp(s)
            if v["detected"]:
                piv, setup = v.get("pivot"), "vcp"
            else:
                piv, setup = detect_pivot(s), "loose"
            if piv and piv <= close <= piv * 1.05:
                days_above = 0
                for x in s.iloc[::-1].iloc[:30]:
                    if x >= piv:
                        days_above += 1
                    else:
                        break
                key = (tk, round(piv, 2))
                if days_above <= MAX_DAYS_ABOVE and key not in seen:
                    kinds.append(("sig", setup))
                seen[key] = True
            if not kinds:
                continue
            entry = Oa[j, k]
            m50, hi = M50[i, k], HI252[i, k]
            hi20 = np.nanmax(Ca[i - 19:i + 1, k])
            feat = {
                "ext50": (close / m50 - 1) * 100, "dist_high": (close / hi - 1) * 100, "rs": RS[i, k],
                "pull20": (close / hi20 - 1) * 100, "regime": regime.iloc[i][mkt[tk]],
                "p200": P200[mkt[tk]][i], "p50": P50[mkt[tk]][i],
                "q": str(dates[i].to_period("Q")), "ticker": tk, "date": dates[i],
            }
            res = {"M0": sim0(k, j, entry)}
            for md in MODES[1:]:
                res[md] = sim_m(k, j, entry, md)
            if any(r is None for r in res.values()):
                continue
            for kind, st in kinds:
                rec = {"kind": kind, "setup": st, **feat}
                for md, (r, out, held) in res.items():
                    rec[f"R_{md}"], rec[f"out_{md}"], rec[f"held_{md}"] = r, out, held
                rows.append(rec)
        if (i - 255) % 250 == 0:
            print(f"  {dates[i].date()} 진행, 누적 {len(rows)}건 ({time.time() - t0:.0f}s)", flush=True)

    B = pd.DataFrame(rows)
    B.to_csv(OUT, index=False, encoding="utf-8-sig")
    S, Z = B[B.kind == "sig"], B[B.kind == "base"]
    print(f"\n진입 신호 {len(S)}건(VCP {int((S.setup == 'vcp').sum())}), 대조군 {len(Z)}건. 저장 {OUT}")
    print("(*) 표본 150 미만. R은 진입가 -8%를 1R로 한 전체 포지션 기준.\n")

    def cell(sub, md):
        n = len(sub)
        if n == 0:
            return "n=0"
        R, o = sub[f"R_{md}"], sub[f"out_{md}"]
        return (f"n={n:5d}{'*' if n < MIN_N else ' '} 평균R {R.mean():+5.2f} 중앙R {R.median():+5.2f} "
                f"플러스 {(R > 0).mean() * 100:3.0f}%  최초손절 {(o == 'stop').mean() * 100:3.0f}%  "
                f"보유 {sub[f'held_{md}'].mean():4.0f}일")

    print("A) 청산 방식 비교 (같은 표본)")
    for name, sub in (("신호 전체", S), ("신호 VCP", S[S.setup == "vcp"]), ("신호 폴백", S[S.setup == "loose"]), ("대조군", Z)):
        print(f"  [{name}]")
        for md in MODES:
            print(f"    {md:4s} {cell(sub, md)}")

    print("\nA2) 신호 vs 대조군, 분기 단위 (M1a 제품 관리)")
    a, b = S.groupby("q").R_M1a.mean(), Z.groupby("q").R_M1a.mean()
    cmpq = pd.DataFrame({"sig": a, "base": b}).dropna()
    cmpq["diff"] = cmpq.sig - cmpq.base
    print(f"  분기 {len(cmpq)}개 중 신호가 높은 분기 {int((cmpq['diff'] > 0).sum())}개, 평균 차이 {cmpq['diff'].mean():+.3f}R "
          f"(표준오차 {cmpq['diff'].std() / np.sqrt(len(cmpq)):.3f})")
    for md in ("M0", "M1b", "M2"):
        a, b = S.groupby("q")[f"R_{md}"].mean(), Z.groupby("q")[f"R_{md}"].mean()
        c2 = pd.DataFrame({"s": a, "b": b}).dropna()
        print(f"  {md}: 신호가 높은 분기 {int((c2.s > c2.b).sum())}/{len(c2)}, 평균 차이 {(c2.s - c2.b).mean():+.3f}R")

    print("\nA3) 국면별 (대조군 / 신호), M1a")
    for reg in ("BULL", "NEUTRAL", "BEAR"):
        print(f"  {reg:7s} 대조군 {cell(Z[Z.regime == reg], 'M1a')}")
        print(f"  {'':7s} 신호   {cell(S[S.regime == reg], 'M1a')}")

    print("\nB) 진입 타이밍 요인 (대조군), 구간별 평균R과 분기 일관성")
    qall = {md: Z.groupby("q")[f"R_{md}"].mean() for md in ("M0", "M1a")}

    def consistency(mask, md):
        g = Z[mask].groupby("q")[f"R_{md}"].agg(["mean", "size"])
        g = g[g["size"] >= 10]
        d = g["mean"] - qall[md].reindex(g.index)
        return len(g), int((d > 0).sum()), d.mean()

    factors = {
        "50일선 위 거리": ("ext50", [(0, 3), (3, 6), (6, 10), (10, 15), (15, 1000)]),
        "52주 고점 대비": ("dist_high", [(-5, 1), (-10, -5), (-15, -10), (-26, -15)]),
        "RS": ("rs", [(70, 80), (80, 90), (90, 101)]),
        "20일 종가 고점 대비": ("pull20", [(-0.001, 1), (-3, -0.001), (-6, -3), (-100, -6)]),
    }
    for fname, (col, bins) in factors.items():
        print(f"  [{fname}]")
        for lo, hi in bins:
            mask = (Z[col] >= lo) & (Z[col] < hi)
            sub = Z[mask]
            if not len(sub):
                continue
            nq, w, dm = consistency(mask, "M1a")
            print(f"    {lo:7.1f}~{hi:7.1f}  n={len(sub):5d}{'*' if len(sub) < MIN_N else ' '} "
                  f"M0 {sub.R_M0.mean():+5.2f}  M1a {sub.R_M1a.mean():+5.2f}   분기 {w}/{nq} 앞섬 평균차 {dm:+.3f}R")
    print(f"\n완료 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
