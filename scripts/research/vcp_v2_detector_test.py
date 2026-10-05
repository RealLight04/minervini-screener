# -*- coding: utf-8 -*-
"""VCP 탐지와 피벗 돌파를 미너비니 정의에 더 가깝게 다시 구현해서 과거 데이터로 시험한다.

현재 detect_vcp의 약점
  1) 스윙을 종가로만, 앞뒤 5일 창으로 잡는다. 최근 5일의 고점과 저점은 확정이 안 돼서 마지막
     수축이 늦게 보이고, 종가 기준이라 조정폭이 실제 장중 조정보다 작게 나온다.
  2) 피벗이 '가장 최근 스윙 고점'이라 마지막 수축(핸들)의 윗선과 다를 수 있다.
  3) 돌파는 종가 > 피벗 하나뿐. 장중 체결, 거래량 확인이 없다.

v2 (미리 정한 정의, 결과를 보고 고치지 않는다)
  지그재그: 장중 고가/저가 사용. 반전 임계 theta = max(3%, 1.5 x ATR14%). 임계만큼 되돌려야 스윙이
            확정된다. 과거 데이터만 쓰는 인과적 상태 기계라 미래를 보지 않고 최신 구간도 반영한다.
  수축: 확정 고점 -> 다음 저점의 낙폭. 아직 확정 안 된 진행 중 조정도 마지막 수축으로 센다.
  베이스: 최근 150일 안에서 가장 높은 확정 고점을 시작으로 하는 연속 조정들.
  VCP(det2): 수축 2개 이상, 첫 수축 <= 35%, 마지막 <= 가장 넓은 수축의 60%, 마지막 <= 12%,
            다시 벌어진 횟수 <= 1. (임계값은 기존 v1과 같게 둬서 '스윙 정의' 효과만 본다)
  피벗: 마지막 수축을 시작한 고점(핸들 윗선).
  돌파(P 이벤트): 전날까지 det2이고 최근 5일 종가가 피벗 이하였다가 오늘 종가가 피벗 위(5% 이내).

대조(D 이벤트): VCP 여부와 무관하게 '직전 40일 장중 고가를 종가로 처음 넘은 날'(5% 이내).
D 이벤트를 VCP 상태로 쪼개서, 같은 돌파 트리거에서 수축 상태가 성과를 가르는지 본다.

진입 두 가지
  E1 신호 다음 날 시가 (앞선 백테스트와 같음)
  E2 돌파선에 걸어둔 매수 주문(장중 체결): 진입가 = max(당일 시가, 돌파선). 당일 저가가 손절선에
     닿으면 손절로 본다(보수적).
청산은 제품 관리 M1a(손절 -8%, 2.5R에서 절반 익절, 나머지 본전 손절 + 50일선 종가 이탈 정리,
90거래일 창). 트렌드 템플릿 + RS 게이트는 앞선 스크립트와 같다. 실적 게이트 없음.

채택 기준(결과 전에 정함): 쪼갠 쪽 표본 150 이상, 20개 분기 중 70% 이상에서 반대쪽보다 앞서고,
평균 차이 +0.05R 이상. E1, E2 모두에서 충족.

선행: python scripts/research/pull_history.py
사용: python scripts/research/vcp_v2_detector_test.py
"""
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.screener import detect_vcp  # noqa: E402
from config import settings  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "prices.sqlite")
OUT = os.path.join(HERE, "data", "vcp_v2_detector_test.csv")
MAX_HOLD, R_TARGET, MAX_ABOVE = 90, 2.5, 0.05
THETA_MIN, THETA_ATR = 0.03, 1.5
WINDOW, DONCH = 150, 40
MIN_N = 150


def vcp_state(swings, d, ext, ext_idx, t):
    """t일까지 반영된 지그재그 상태에서 베이스의 수축 목록과 피벗을 만든다."""
    lo = t - WINDOW
    recent = []
    for s in reversed(swings):
        if s[0] < lo:
            break
        recent.append(s)
    recent.reverse()
    while recent and recent[0][2] != "H":
        recent.pop(0)
    pbs = []                                   # (고점idx, 고점가, 저점가)
    for i in range(0, len(recent) - 1, 2):
        if recent[i][2] == "H" and recent[i + 1][2] == "L":
            pbs.append((recent[i][0], recent[i][1], recent[i + 1][1]))
    if d == "down" and recent and recent[-1][2] == "H":
        pbs.append((recent[-1][0], recent[-1][1], ext))      # 진행 중인 조정
    if not pbs:
        return None
    i0 = max(range(len(pbs)), key=lambda i: (pbs[i][1], -i))
    pbs = pbs[i0:]
    depths = [(h - l) / h for _, h, l in pbs if h > 0]
    if not depths:
        return None
    widest, last = max(depths), depths[-1]
    upt = sum(1 for i in range(len(depths) - 1) if depths[i + 1] > depths[i])
    det = len(depths) >= 2 and depths[0] <= 0.35 and last <= widest * 0.6 and last <= 0.12 and upt <= 1
    return {"n": len(depths), "d1": depths[0], "last": last, "widest": widest, "upt": upt,
            "pivot": pbs[-1][1], "det2": bool(det)}


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
    Oa, Ha, La, Ca, Va = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy(), V.to_numpy()

    cf, vf = C.ffill(), V.ffill()
    ma50, ma150, ma200 = (cf.rolling(n).mean() for n in (50, 150, 200))
    M50 = ma50.to_numpy()
    hi252, lo252 = cf.rolling(252).max(), cf.rolling(252).min()
    ret52 = cf / cf.shift(252) - 1
    vol50 = vf.rolling(50).mean()
    rs = ret52.copy()
    regime = pd.DataFrame(index=dates, columns=sorted(mkt.dropna().unique()), dtype=object)
    for m in regime.columns:
        cols = [t for t in tickers if mkt[t] == m]
        rs[cols] = ret52[cols].rank(axis=1, pct=True) * 100
        p200 = (cf[cols] > ma200[cols]).where(ma200[cols].notna()).mean(axis=1) * 100
        p50 = (cf[cols] > ma50[cols]).where(ma50[cols].notna()).mean(axis=1) * 100
        regime[m] = np.where((p200 >= 60) & (p50 >= 50), "BULL", np.where(p200 < 40, "BEAR", "NEUTRAL"))
    RS = rs.to_numpy()
    minp = pd.Series([settings.MIN_PRICE if mkt[t] == "US" else settings.MIN_PRICE_KR for t in tickers], index=tickers)
    gate = ((cf > ma150) & (cf > ma200) & (ma150 > ma200) & (ma50 > ma150) & (ma50 > ma200)
            & (ma200 > ma200.shift(21)) & (cf > ma50) & (cf >= lo252 * 1.30) & (cf >= hi252 * 0.75)
            & (rs >= 100 - settings.RS_TOP_PERCENTILE) & cf.ge(minp, axis=1) & (vol50 >= settings.MIN_VOLUME))
    gate = gate.fillna(False).to_numpy()

    # 변동성 지표 (모두 t-1까지의 값으로 쓴다)
    pc = cf.shift(1)
    tr = pd.DataFrame(np.fmax(np.fmax((H - L).to_numpy(), (H - pc).abs().to_numpy()), (L - pc).abs().to_numpy()),
                      index=dates, columns=tickers)
    atr14 = tr.rolling(14).mean()
    ATRP = (atr14 / cf).to_numpy()
    ATR10_50 = (tr.rolling(10).mean() / tr.rolling(50).mean()).to_numpy()
    hh40 = H.shift(1).rolling(DONCH).max().to_numpy()                       # 오늘 제외 직전 40일 장중 고가
    VOL50 = vol50.to_numpy()
    rng10 = ((H.rolling(10).max() - L.rolling(10).min()) / H.rolling(10).max()).to_numpy()
    print(f"{T}거래일 {dates[0].date()}~{dates[-1].date()}, {len(tickers)}종목, 준비 {time.time() - t0:.0f}s", flush=True)

    def sim_m(k, start, entry, rp=0.08):
        risk = entry * rp
        stop, target = entry - risk, entry + R_TARGET * risk
        half, realized, rem_stop, exit_next, last = False, 0.0, stop, False, None
        for d in range(start, start + MAX_HOLD):
            o, hi, lo, cl = Oa[d, k], Ha[d, k], La[d, k], Ca[d, k]
            w = 0.5 if half else 1.0
            if exit_next and not np.isnan(o):
                return realized + w * (o - entry) / risk
            if d > start and not np.isnan(o) and o <= rem_stop:
                return realized + w * (o - entry) / risk
            if not np.isnan(lo) and lo <= rem_stop:
                return realized + w * (rem_stop - entry) / risk
            if not half:
                gap_up = d > start and not np.isnan(o) and o >= target
                if gap_up or (not np.isnan(hi) and hi >= target):
                    fill = o if gap_up else target
                    realized, half, rem_stop = 0.5 * (fill - entry) / risk, True, entry
            if not np.isnan(cl):
                last = cl
                m = M50[d, k]
                if half and not np.isnan(m) and cl < m:
                    exit_next = True
        if last is None:
            return None
        w = 0.5 if half else 1.0
        return realized + w * (last - entry) / risk

    rows = []
    cover = {"gate_days": 0, "det1": 0, "det2": 0, "both": 0}
    first, last_i = 255, T - 2 - MAX_HOLD
    for k, tk in enumerate(tickers):
        swings, d, ext, ext_idx = [], "up", np.nan, -1
        for t in range(T - 1):
            h, l, c = Ha[t, k], La[t, k], Ca[t, k]
            if np.isnan(h) or np.isnan(l) or np.isnan(c):
                continue
            # ① 오늘 이벤트 판정 (상태는 t-1까지)
            if first <= t <= last_i and (gate[t, k] or gate[t - 1, k]) and not np.isnan(ext) and not np.isnan(Oa[t + 1, k]):
                st = vcp_state(swings, d, ext, ext_idx, t - 1)
                lvl_d = hh40[t, k]
                recent_c = Ca[t - 5:t, k]
                no_recent = not np.isnan(recent_c).all() and np.nanmax(recent_c) <= (lvl_d if not np.isnan(lvl_d) else 0)
                ev = []
                hi5 = Ha[t - 5:t, k]
                no_recent_hi = not np.isnan(hi5).all()
                g_now, g_prev = bool(gate[t, k]), bool(gate[t - 1, k])
                if g_now and not np.isnan(lvl_d) and c > lvl_d and c <= lvl_d * (1 + MAX_ABOVE) and no_recent:
                    ev.append(("D", lvl_d))
                if g_now and st is not None and st["det2"]:
                    pv = st["pivot"]
                    rc = Ca[t - 5:t, k]
                    if c > pv and c <= pv * (1 + MAX_ABOVE) and not np.isnan(rc).all() and np.nanmax(rc) <= pv:
                        ev.append(("P", pv))
                # 장중 돌파: 어제까지의 정보로만 정의 (오늘 종가를 보지 않음). 돌파선에 걸어둔 매수 주문 체결
                if g_prev and not np.isnan(lvl_d) and no_recent_hi and h >= lvl_d and np.nanmax(hi5) <= lvl_d                         and max(Oa[t, k], lvl_d) <= lvl_d * (1 + MAX_ABOVE):
                    ev.append(("DI", lvl_d))
                if g_prev and st is not None and st["det2"] and no_recent_hi:
                    pv = st["pivot"]
                    if h >= pv and np.nanmax(hi5) <= pv and max(Oa[t, k], pv) <= pv * (1 + MAX_ABOVE):
                        ev.append(("PI", pv))
                if g_prev and t % 5 == 0:
                    ev.append(("B0", np.nan))      # 대조: 어제 게이트 통과 종목을 오늘 시가에 매수
                if g_now and t % 5 == 0:
                    ev.append(("B", np.nan))
                if st is not None:
                    cover["gate_days"] += 1
                    cover["det2"] += int(st["det2"])
                for kind, lvl in ev:
                    s_ = Ca[:t, k]
                    v1 = False
                    if kind not in ("B", "B0"):
                        ser = pd.Series(s_[~np.isnan(s_)])
                        v1 = bool(detect_vcp(ser)["detected"]) if len(ser) >= 60 else False
                    e1 = Oa[t + 1, k]
                    intraday = kind in ("DI", "PI", "B0")
                    e2 = (Oa[t, k] if kind == "B0" else max(Oa[t, k], lvl)) if kind != "B" else np.nan
                    rp = min(0.10, max(0.04, 2.0 * ATRP[t - 1, k])) if not np.isnan(ATRP[t - 1, k]) else np.nan
                    r1 = sim_m(k, t + 1, e1) if not intraday else None
                    r2 = sim_m(k, t, e2) if kind != "B" else None
                    r1n = sim_m(k, t + 1, e1, rp) if (not intraday and not np.isnan(rp)) else None
                    r2n = sim_m(k, t, e2, rp) if (kind != "B" and not np.isnan(rp)) else None
                    vr = Va[t, k] / VOL50[t - 1, k] if VOL50[t - 1, k] else np.nan
                    rows.append({
                        "kind": kind, "ticker": tk, "date": dates[t], "q": str(dates[t].to_period("Q")),
                        "mkt": mkt[tk], "regime": regime[mkt[tk]].iloc[t], "rs": RS[t, k],
                        "r1": r1, "r2": r2, "r1n": r1n, "r2n": r2n, "rp": rp, "v1": v1,
                        "det2": bool(st["det2"]) if st else False, "n": st["n"] if st else 0,
                        "last": st["last"] if st else np.nan, "d1": st["d1"] if st else np.nan,
                        "rng10": rng10[t - 1, k], "atr_ratio": ATR10_50[t - 1, k], "atrp": ATRP[t - 1, k],
                        "volx": vr, "gap_pivot": (lvl / st["pivot"] - 1) if (st and kind == "D") else np.nan,
                    })
            # ② 상태 갱신 (오늘 봉 반영)
            th = max(THETA_MIN, THETA_ATR * (ATRP[t - 1, k] if t > 0 and not np.isnan(ATRP[t - 1, k]) else 0.0))
            if np.isnan(ext):
                d, ext, ext_idx = "up", h, t
            elif d == "up":
                if h > ext:
                    ext, ext_idx = h, t
                elif l <= ext * (1 - th):
                    swings.append((ext_idx, ext, "H"))
                    d, ext, ext_idx = "down", l, t
            else:
                if l < ext:
                    ext, ext_idx = l, t
                elif h >= ext * (1 + th):
                    swings.append((ext_idx, ext, "L"))
                    d, ext, ext_idx = "up", h, t
        if (k + 1) % 100 == 0:
            print(f"  {k + 1}/{len(tickers)}종목 {time.time() - t0:.0f}s, 이벤트 {len(rows)}", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n이벤트 {len(df)}건 ({df.kind.value_counts().to_dict()}), 총 {time.time() - t0:.0f}s")
    report(df)
    report_norm(df)
    report_intraday(df)


def qcmp(df, mask, col):
    a, b = df[mask], df[~mask]
    qs = sorted(set(a.q) & set(b.q))
    wins, used = 0, 0
    for q in qs:
        x, y = a[a.q == q][col].dropna(), b[b.q == q][col].dropna()
        if len(x) >= 5 and len(y) >= 5:
            used += 1
            wins += int(x.mean() > y.mean())
    return wins, used


def line(name, df, mask, cols=(("r1", "E1"), ("r2", "E2"))):
    a, b = df[mask], df[~mask]
    out = f"{name:<34} n={len(a):>5}"
    for col, lab in cols:
        x, y = a[col].dropna(), b[col].dropna()
        if len(x) < 5 or len(y) < 5:
            out += f" | {lab} n/a"
            continue
        w, u = qcmp(df, mask, col)
        diff = x.mean() - y.mean()
        ok = len(x) >= MIN_N and u and w / u >= 0.7 and diff >= 0.05
        out += f" | {lab} {x.mean():+.2f}R vs {y.mean():+.2f}R ({diff:+.2f}) 분기 {w}/{u}{' ✔' if ok else ''}"
    print(out)


def report(df):
    d = df[df.kind == "D"].copy()
    p = df[df.kind == "P"].copy()
    b = df[df.kind == "B"].copy()
    print("\n=== 기본 성과 (M1a 청산) ===")
    for name, x in (("B 트렌드템플릿 아무 날", b), ("D 40일 고가 돌파 전체", d), ("P v2 피벗 돌파", p)):
        s1 = x.r1.dropna()
        s2 = x.r2.dropna()
        print(f"{name:<24} n={len(x):>5} E1 {s1.mean():+.3f}R" + (f" E2 {s2.mean():+.3f}R" if len(s2) else ""))

    print("\n=== D 돌파를 수축 상태로 쪼개기 (같은 트리거, 상태만 다름) ===")
    line("v1(현행) VCP였음", d, d.v1)
    line("v2 VCP였음(det2)", d, d.det2)
    line("v2 VCP + 수축 3개 이상", d, d.det2 & (d.n >= 3))
    line("직전 10일 변동폭 8% 이하", d, d.rng10 <= 0.08)
    line("ATR10/ATR50 0.8 이하(변동성 수축)", d, d.atr_ratio <= 0.8)
    line("거래량 1.4배 이상 돌파", d, d.volx >= 1.4)
    line("RS 90 이상", d, d.rs >= 90)

    print("\n=== P(v2 피벗 돌파) vs 대조 ===")
    pp = pd.concat([p.assign(g=1), d[~d.det2].assign(g=0)])
    line("P vs D 중 VCP 아님", pp, pp.g == 1)
    pb = pd.concat([p.assign(g=1), b.assign(g=0)])
    line("P vs 트렌드템플릿 아무 날", pb, pb.g == 1)
    line("P 중 거래량 1.4배 이상", p, p.volx >= 1.4)

    print("\n=== 국면별 (D 중 det2 여부) ===")
    for r in ("BULL", "NEUTRAL", "BEAR"):
        x = d[d.regime == r]
        if len(x) >= 20:
            line(f"{r} det2", x, x.det2)


def report_intraday(df):
    di, pi, b0 = (df[df.kind == x].copy() for x in ("DI", "PI", "B0"))
    print("\n=== 장중 돌파 매수 (돌파선 체결, 어제 정보로만 정의). E2 기준 ===")
    for name, x in (("B0 어제 게이트 종목 시가 매수", b0), ("DI 40일 고가 장중 돌파", di), ("PI v2 피벗 장중 돌파", pi)):
        print(f"  {name:<28} n={len(x):>5} E2 {x.r2.mean():+.3f}R  ATR정규화 {x.r2n.mean():+.3f}R")
    C2 = (("r2", "E2"), ("r2n", "E2n"))
    pb = pd.concat([pi.assign(g=1), b0.assign(g=0)])
    line("PI vs B0", pb, pb.g == 1, C2)
    pd_ = pd.concat([pi.assign(g=1), di[~di.det2].assign(g=0)])
    line("PI vs DI 중 VCP 아님", pd_, pd_.g == 1, C2)
    db = pd.concat([di.assign(g=1), b0.assign(g=0)])
    line("DI vs B0", db, db.g == 1, C2)
    print("  DI 쪼개기")
    line("  v1(현행) VCP였음", di, di.v1, C2)
    line("  v2 VCP였음(det2)", di, di.det2, C2)
    line("  직전 10일 변동폭 8% 이하", di, di.rng10 <= 0.08, C2)
    line("  RS 90 이상", di, di.rs >= 90, C2)


def report_norm(df):
    NC = (("r1n", "E1n"), ("r2n", "E2n"))
    d = df[df.kind == "D"].copy()
    p = df[df.kind == "P"].copy()
    b = df[df.kind == "B"].copy()
    print("\n=== 변동성 구간별 (고정 -8% 손절 R). 조용한 종목일수록 R이 작게 나오는지 ===")
    d["vq"] = pd.qcut(d.atrp, 3, labels=["저변동", "중변동", "고변동"])
    for q, g in d.groupby("vq", observed=True):
        print(f"  D {q} n={len(g):>4} ATR% {g.atrp.mean() * 100:.1f} E1 {g.r1.mean():+.2f}R E2 {g.r2.mean():+.2f}R "
              f"| ATR정규화 E1n {g.r1n.mean():+.2f}R E2n {g.r2n.mean():+.2f}R")
    print("\n=== ATR 정규화 손절(2xATR, 4~10%) 기준 ===")
    for name, x in (("B 트렌드템플릿 아무 날", b), ("D 40일 고가 돌파", d), ("P v2 피벗 돌파", p)):
        print(f"  {name:<22} n={len(x):>5} E1n {x.r1n.mean():+.3f}R" + (f" E2n {x.r2n.mean():+.3f}R" if x.r2n.notna().any() else ""))
    print("\n--- D 돌파 쪼개기 (정규화) ---")
    line("v1(현행) VCP였음", d, d.v1, NC)
    line("v2 VCP였음(det2)", d, d.det2, NC)
    line("v2 VCP + 수축 3개 이상", d, d.det2 & (d.n >= 3), NC)
    line("직전 10일 변동폭 8% 이하", d, d.rng10 <= 0.08, NC)
    line("거래량 1.4배 이상 돌파", d, d.volx >= 1.4, NC)
    line("RS 90 이상", d, d.rs >= 90, NC)
    print("\n--- P vs 대조 (정규화) ---")
    pp = pd.concat([p.assign(g=1), d[~d.det2].assign(g=0)])
    line("P vs D 중 VCP 아님", pp, pp.g == 1, NC)
    pb = pd.concat([p.assign(g=1), b.assign(g=0)])
    line("P vs 트렌드템플릿 아무 날", pb, pb.g == 1, NC[:1])
    line("P 중 거래량 1.4배 이상", p, p.volx >= 1.4, NC)


if __name__ == "__main__":
    main()
