# -*- coding: utf-8 -*-
"""제품 STRONG_BUY 상태의 기대값 백테스트 (2021~, 점 시점 재현).

질문: 라이브 결과(2026-07~10)에서 제품 규칙(진입가 -8%, 목표 +20%)의 기대값이 -4.3%였다.
이게 그 구간 탓인지 구조인지, 그리고 무엇을 고치면 나아지는지 본다.

재현하는 것(screen_stock과 같은 정의, 점 시점)
  - 트렌드 템플릿 8개 + 유동성(최소 주가, 평균 거래량). 실적 게이트는 과거 데이터가 없어 못 건다
    (실제 신호보다 모집단이 넓다).
  - 피벗 = VCP가 감지되면 detect_vcp의 피벗, 아니면 detect_pivot(느슨한 폴백). 둘을 따로 본다.
  - STRONG_BUY 상태 = 피벗 <= 종가 <= 피벗 x 1.05.
진입: 신호 다음 날 시가. 신호를 본 사람이 살 수 있는 가격이다.
청산: 손절 또는 목표(리스크의 2.5배) 중 먼저 닿는 쪽, 일중 고저 사용, 갭은 시가 체결,
      같은 날 둘 다면 손절 우선, MAX_HOLD거래일 안에 안 닿으면 마지막 종가로 평가(timeout).
성과는 R 배수(이익/리스크)로 보고한다. 손절 거리가 달라도 같은 잣대로 비교하려는 것이다.

손절 변형
  fixed8  : 진입가 -8% (제품 현재 규칙)
  fixed5  : 진입가 -5%
  lastlow : 최근 10거래일 최저가의 1% 아래, 단 리스크를 3%~8%로 제한(미너비니의 논리적 지점)
  atr     : 진입가 - 2.5 x ATR14, 단 리스크를 3%~8%로 제한

채택 기준(결과를 보기 전에 정해둔다, 과적합 방지)
  1) 표본 150 이상인 칸만 판단한다.
  2) 강세장과 혼조장 둘 다에서 평균 R이 개선돼야 한다.
  3) 2021~2023과 2024~ 두 구간 모두에서 같은 방향이어야 한다.

주의: 같은 에피소드의 여러 날 진입(경과일 분석용)은 서로 겹친다. 변형 비교와 국면별 표는
에피소드당 첫 진입만 쓴다.

선행: python scripts/research/pull_history.py   (시가·고가·저가 포함 버전)
사용: python scripts/research/vcp_expectancy_backtest.py
출력: scripts/research/data/vcp_expectancy_backtest.csv
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

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "prices.sqlite")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vcp_expectancy_backtest.csv")
OUT_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vcp_expectancy_baseline.csv")
MAX_HOLD = 60
MAX_DAYS_ABOVE = 10
R_TARGET = 2.5
MIN_N = 150
VARIANTS = ["fixed8", "fixed5", "lastlow", "atr"]


def main():
    BASE = "--baseline" in sys.argv   # 대조군: 피벗 조건 없이 추세 템플릿 통과 종목을 5거래일마다 진입
    t0 = time.time()
    con = sqlite3.connect(DB)
    px = pd.read_sql("SELECT ticker, market, date, open, high, low, close, volume FROM prices", con, parse_dates=["date"])
    con.close()
    if px["open"].isna().all():
        print("시가·고가·저가가 없는 가격 DB입니다. pull_history.py를 새 버전으로 다시 돌리세요.")
        return

    def mat(col):
        return px.pivot(index="date", columns="ticker", values=col).sort_index()

    C, O, H, L, V = mat("close"), mat("open"), mat("high"), mat("low"), mat("volume")
    tickers = list(C.columns)
    kidx = {t: n for n, t in enumerate(tickers)}
    dates = C.index
    T = len(dates)
    mkt = px.drop_duplicates("ticker").set_index("ticker")["market"].reindex(tickers)
    Oa, Ha, La, Ca = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy()
    print(f"데이터 {T}거래일 {dates[0].date()}~{dates[-1].date()}, {len(tickers)}종목", flush=True)

    cf, vf = C.ffill(), V.ffill()
    ma50, ma150, ma200 = (cf.rolling(n).mean() for n in (50, 150, 200))
    ma200_prev = ma200.shift(21)
    hi252, lo252 = cf.rolling(252).max(), cf.rolling(252).min()
    ret52 = cf / cf.shift(252) - 1
    vol50 = vf.rolling(50).mean()

    # 시장별 RS 백분위와 시장별 국면(제품과 같은 정의)
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

    minp = pd.Series([settings.MIN_PRICE if mkt[t] == "US" else settings.MIN_PRICE_KR for t in tickers], index=tickers)
    gate = ((cf > ma150) & (cf > ma200) & (ma150 > ma200) & (ma50 > ma150) & (ma50 > ma200)
            & (ma200 > ma200_prev) & (cf > ma50) & (cf >= lo252 * 1.30) & (cf >= hi252 * 0.75)
            & (rs >= 100 - settings.RS_TOP_PERCENTILE) & cf.ge(minp, axis=1) & (vol50 >= settings.MIN_VOLUME))
    gate = gate.fillna(False).to_numpy()
    print(f"지표 준비 {time.time() - t0:.0f}s", flush=True)

    def simulate(k, j, entry, stop, end):
        """j일 시가 진입. (R, 결과, 보유일). 판정 불가(자료 부족)면 None."""
        risk = entry - stop
        target = entry + R_TARGET * risk
        last = None
        for d in range(j, end):
            o, hi, lo, cl = Oa[d, k], Ha[d, k], La[d, k], Ca[d, k]
            if d > j and not np.isnan(o) and o <= stop:
                return (o - entry) / risk, "stop", d - j + 1
            if not np.isnan(lo) and lo <= stop:
                return (stop - entry) / risk, "stop", d - j + 1
            if d > j and not np.isnan(o) and o >= target:
                return (o - entry) / risk, "target", d - j + 1
            if not np.isnan(hi) and hi >= target:
                return R_TARGET, "target", d - j + 1
            if not np.isnan(cl):
                last = cl
        if end - j < MAX_HOLD or last is None:
            return None  # 아직 MAX_HOLD가 안 지난 최근 진입은 결판 불가 → 제외
        return (last - entry) / risk, "timeout", end - j

    seen = {}
    rows = []
    for i in range(255, T - 1):
        cand = np.flatnonzero(gate[i])
        if not len(cand):
            continue
        j = i + 1
        end = min(j + MAX_HOLD, T)
        for k in cand:
            tk = tickers[k]
            if np.isnan(Ca[i, k]) or np.isnan(Oa[j, k]):
                continue
            if BASE and i % 5:
                continue
            s = C[tk].iloc[:i + 1].dropna()
            if len(s) < 200:
                continue
            if BASE:
                piv, setup, close, days_above, first = float(s.iloc[-1]), "base", float(s.iloc[-1]), 0, True
            else:
                v = detect_vcp(s)
                if v["detected"]:
                    piv, setup = v.get("pivot"), "vcp"
                else:
                    piv, setup = detect_pivot(s), "loose"
                if not piv:
                    continue
                close = float(s.iloc[-1])
                if not (piv <= close <= piv * 1.05):
                    continue
                days_above = 0
                for x in s.iloc[::-1].iloc[:30]:
                    if x >= piv:
                        days_above += 1
                    else:
                        break
                if days_above > MAX_DAYS_ABOVE:
                    continue
                key = (tk, round(piv, 2))
                first = key not in seen
                seen[key] = True

            entry = Oa[j, k]
            lows = La[i - 9:i + 1, k]
            if np.isnan(lows).all():
                continue
            sl = np.nanmin(lows) * 0.99
            hh, ll, cc = Ha[i - 13:i + 1, k], La[i - 13:i + 1, k], Ca[i - 14:i, k]
            tr = np.nanmax(np.vstack([hh - ll, np.abs(hh - cc), np.abs(ll - cc)]), axis=0)
            atr = np.nanmean(tr)
            if np.isnan(atr):
                continue
            lo_cap, hi_cap = entry * 0.92, entry * 0.97
            stops = {
                "fixed8": entry * 0.92,
                "fixed5": entry * 0.95,
                "lastlow": min(max(sl, lo_cap), hi_cap),
                "atr": min(max(entry - 2.5 * atr, lo_cap), hi_cap),
            }
            rec = {
                "ticker": tk, "date": dates[i], "setup": setup, "regime": regime.iloc[i][mkt[tk]],
                "days_above": days_above, "ext": (close / piv - 1) * 100, "first": first,
                "p200": P200[mkt[tk]][i], "p50": P50[mkt[tk]][i],
                "period": "2021-2023" if dates[i].year <= 2023 else "2024+",
            }
            ok = True
            for name, st in stops.items():
                res = simulate(k, j, entry, st, end)
                if res is None:
                    ok = False
                    break
                rec[f"R_{name}"], rec[f"out_{name}"], rec[f"held_{name}"] = res
                rec[f"risk_{name}"] = (entry - st) / entry * 100
            if ok:
                rows.append(rec)
        if (i - 255) % 250 == 0:
            print(f"  {dates[i].date()} 진행, 누적 진입 {len(rows)}건 ({time.time() - t0:.0f}s)", flush=True)

    B = pd.DataFrame(rows)
    if BASE:
        B.to_csv(OUT_BASE, index=False, encoding="utf-8-sig")
        print(f"\n대조군 {len(B)}건 저장 {OUT_BASE}")
        for reg in ("BULL", "NEUTRAL", "BEAR", None):
            sub = B if reg is None else B[B.regime == reg]
            o = sub.out_fixed8
            print(f"  {reg or '전체':7s} n={len(sub):6d} 평균R {sub.R_fixed8.mean():+5.2f} 목표 {(o == 'target').mean() * 100:4.1f}% "
                  f"손절 {(o == 'stop').mean() * 100:4.1f}% 시간종료 {(o == 'timeout').mean() * 100:4.1f}%")
        return
    B.to_csv(OUT, index=False, encoding="utf-8-sig")
    F = B[B["first"]]
    print(f"\n진입 {len(B)}건 (에피소드 첫 진입 {len(F)}건), 저장 {OUT}\n")

    def cell(sub, var):
        n = len(sub)
        if n == 0:
            return "n=0"
        R = sub[f"R_{var}"]
        o = sub[f"out_{var}"]
        mark = "*" if n < MIN_N else " "
        return (f"n={n:5d}{mark} 평균R {R.mean():+5.2f}  중앙R {R.median():+5.2f}  "
                f"목표 {(o == 'target').mean() * 100:4.1f}%  손절 {(o == 'stop').mean() * 100:4.1f}%  "
                f"시간종료 {(o == 'timeout').mean() * 100:4.1f}%")

    print("(*) 표본 150 미만. 손익분기 승률은 목표 2.5R 기준 약 28.6%.\n")
    print("1) 손절 규칙 비교: 에피소드 첫 진입만")
    for setup in ("전체", "vcp", "loose"):
        sub = F if setup == "전체" else F[F.setup == setup]
        print(f"  [{setup}] 평균 리스크 fixed8 {sub.risk_fixed8.mean():.1f}%, lastlow {sub.risk_lastlow.mean():.1f}%, atr {sub.risk_atr.mean():.1f}%")
        for var in VARIANTS:
            print(f"    {var:8s} {cell(sub, var)}")

    print("\n2) 국면별 (fixed8 / lastlow), 첫 진입만")
    for setup in ("vcp", "loose"):
        for reg in ("BULL", "NEUTRAL", "BEAR"):
            sub = F[(F.setup == setup) & (F.regime == reg)]
            print(f"  {setup:5s} {reg:7s} fixed8  {cell(sub, 'fixed8')}")
            print(f"  {'':5s} {'':7s} lastlow {cell(sub, 'lastlow')}")

    print("\n3) 시기별 (fixed8 / lastlow), 첫 진입만")
    for setup in ("vcp", "loose"):
        for per in ("2021-2023", "2024+"):
            sub = F[(F.setup == setup) & (F.period == per)]
            print(f"  {setup:5s} {per:9s} fixed8  {cell(sub, 'fixed8')}")
            print(f"  {'':5s} {'':9s} lastlow {cell(sub, 'lastlow')}")

    print("\n4) 돌파 후 경과일별 (fixed8), 모든 진입(겹침 있음)")
    for setup in ("vcp", "loose"):
        for lo, hi, lab in ((1, 1, "1일(돌파 당일)"), (2, 3, "2~3일"), (4, 6, "4~6일"), (7, 10, "7~10일")):
            sub = B[(B.setup == setup) & (B.days_above >= lo) & (B.days_above <= hi)]
            print(f"  {setup:5s} {lab:12s} {cell(sub, 'fixed8')}")

    print("\n5) 연장폭별 (fixed8), 첫 진입만")
    for setup in ("vcp", "loose"):
        for lo, hi, lab in ((0, 1, "0~1%"), (1, 2, "1~2%"), (2, 3.5, "2~3.5%"), (3.5, 5.01, "3.5~5%")):
            sub = F[(F.setup == setup) & (F.ext >= lo) & (F.ext < hi)]
            print(f"  {setup:5s} {lab:7s} {cell(sub, 'fixed8')}")
    print("\n6) 시장 폭별 (fixed8), 첫 진입만")
    for col in ("p50", "p200"):
        edges = [0, 30, 40, 50, 60, 70, 101]
        for lo, hi in zip(edges[:-1], edges[1:]):
            sub = F[(F[col] >= lo) & (F[col] < hi)]
            print(f"  {col:4s} {lo:3d}~{min(hi, 100):3d}%  {cell(sub, 'fixed8')}")
    print("\n7) 시장 게이트 비교 (이 조건이면 매수 신호를 막는다고 가정), fixed8, 첫 진입만")
    gates = {
        "게이트 없음": F.p200 >= -1,
        "현재: 200일선 40% 미만 차단": F.p200 >= 40,
        "50일선 30% 미만 차단": F.p50 >= 30,
        "50일선 40% 미만 차단": F.p50 >= 40,
        "50일선 50% 미만 차단": F.p50 >= 50,
        "200일선 50% 미만 차단": F.p200 >= 50,
        "둘 다 40% 미만이면 차단": ~((F.p50 < 40) & (F.p200 < 40)),
    }
    for name, mask in gates.items():
        sub = F[mask]
        print(f"  {name:22s} {cell(sub, 'fixed8')}  (통과 {len(sub) / len(F) * 100:3.0f}%)")
    print(f"\n완료 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
