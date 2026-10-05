# -*- coding: utf-8 -*-
"""app/vcp.py 점수 모델 백테스트 + Jev 비교. 2010~2026, 연구용(앱과 DB를 건드리지 않는다).

이벤트(V): 트렌드 템플릿 + RS 70 이상 종목에서, 전날까지의 데이터로 찾은 VCP 형성의 피벗을 종가로 처음 넘은 날.
  피벗 위 5% 이내, 그 형성에서 처음 넘은 날 하나만, 베이스 저점은 지켜진 상태. 점수는 돌파 전날 모습 기준이다.
  진입은 다음 날 시가(E1), 청산은 제품 관리(M1a). R은 고정 -8% 손절(r1)과 ATR 정규화(r1n) 두 가지.
대조: vcp_v2_detector_test.py가 만든 D(40일 고가 종가 돌파)와 B(트렌드 템플릿 아무 날) 이벤트.

보는 것
  1. 점수 등급별 평균 R, 자격(60점 이상) 대 미만, 분기별 일관성(채택 기준: 표본 150 이상, 분기 70% 이상, +0.05R 이상)
  2. 돌파 거래량 등급, 항목별 점수와 R의 상관(탐색용, 채택 근거로 쓰지 않는다)
  3. Jev: 항목 수치만 보여주고 수익으로 끝날지 물어, 점수 합계와 RS의 AUC와 나란히 비교
점수 가중치를 이 결과에 맞춰 고치지 않는다(같은 과거 데이터라 검증이 아니라 과적합이다).

선행: vcp_v2_detector_test.py 실행(CSV 생성), Jev는 .env 에 TYPESAFE_API_KEY
사용: python scripts/research/vcp_score_backtest.py [--no-jev] [--per-period N]
출력: scripts/research/data/vcp_score_events.csv, vcp_score_jev.csv
"""
import argparse
import asyncio
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from app import vcp  # noqa: E402
from config import settings  # noqa: E402

DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "prices.sqlite")
BASE_CSV = os.path.join(DATA, "vcp_v2_detector_test.csv")
OUT = os.path.join(DATA, "vcp_score_events.csv")
OUT_JEV = os.path.join(DATA, "vcp_score_jev.csv")
MAX_HOLD, R_TARGET, MAX_ABOVE = 90, 2.5, 0.05
MIN_N = 150
COLS = (("r1", "E1"), ("r1n", "E1n"))
MODEL = os.getenv("JEV_MODEL", "jev-1.13.0")        # 버전 고정(jev-latest는 움직이는 별칭이라 재현성이 깨진다)
GATEWAY_URL = os.getenv("JEV_GATEWAY_URL", "https://ai-gateway.vercel.sh/typesafe")


def build_events():
    t0 = time.time()
    con = sqlite3.connect(DB)
    px = pd.read_sql("SELECT ticker, market, date, open, high, low, close, volume FROM prices", con, parse_dates=["date"])
    con.close()

    def mat(col):
        return px.pivot(index="date", columns="ticker", values=col).sort_index()

    C, O, H, L, V = mat("close"), mat("open"), mat("high"), mat("low"), mat("volume")
    tickers, dates = list(C.columns), C.index
    T = len(dates)
    mkt = px.drop_duplicates("ticker").set_index("ticker")["market"].reindex(tickers)
    Oa, Ha, La, Ca, Va = O.to_numpy(), H.to_numpy(), L.to_numpy(), C.to_numpy(), V.to_numpy()

    # 게이트: 홈 후보와 같은 기준(트렌드 템플릿 + RS + 가격/거래량 하한). vcp_v2_detector_test.py와 같은 식이다
    cf, vf = C.ffill(), V.ffill()
    ma50, ma150, ma200 = (cf.rolling(n).mean() for n in (50, 150, 200))
    M50 = ma50.to_numpy()
    hi252, lo252 = cf.rolling(252).max(), cf.rolling(252).min()
    ret52 = cf / cf.shift(252) - 1
    vol50 = vf.rolling(50).mean()
    rs = ret52.copy()
    for m in sorted(mkt.dropna().unique()):
        cols = [t for t in tickers if mkt[t] == m]
        rs[cols] = ret52[cols].rank(axis=1, pct=True) * 100
    RS = rs.to_numpy()
    minp = pd.Series([settings.MIN_PRICE if mkt[t] == "US" else settings.MIN_PRICE_KR for t in tickers], index=tickers)
    gate = ((cf > ma150) & (cf > ma200) & (ma150 > ma200) & (ma50 > ma150) & (ma50 > ma200)
            & (ma200 > ma200.shift(21)) & (cf > ma50) & (cf >= lo252 * 1.30) & (cf >= hi252 * 0.75)
            & (rs >= 100 - settings.RS_TOP_PERCENTILE) & cf.ge(minp, axis=1) & (vol50 >= settings.MIN_VOLUME))
    gate = gate.fillna(False).to_numpy()
    pc = cf.shift(1)
    tr = pd.DataFrame(np.fmax(np.fmax((H - L).to_numpy(), (H - pc).abs().to_numpy()), (L - pc).abs().to_numpy()),
                      index=dates, columns=tickers)
    ATRP = (tr.rolling(14).mean() / cf).to_numpy()
    prev10 = cf.shift(1).rolling(10).max().to_numpy()
    nxt_open = ~np.isnan(np.vstack([Oa[1:], np.full((1, len(tickers)), np.nan)]))
    first, last_i = 255, T - 2 - MAX_HOLD
    cand = gate & (Ca > prev10) & nxt_open
    cand[:first] = False
    cand[last_i + 1:] = False
    print(f"{T}거래일 {dates[0].date()}~{dates[-1].date()}, {len(tickers)}종목, 후보일 {int(cand.sum())}, 준비 {time.time() - t0:.0f}s", flush=True)

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

    rows, seen = [], set()
    for k, tk in enumerate(tickers):
        ts = np.nonzero(cand[:, k])[0]
        if not len(ts):
            continue
        valid = np.nonzero(~(np.isnan(Ca[:, k]) | np.isnan(Ha[:, k]) | np.isnan(La[:, k])))[0]
        pos = {int(t): i for i, t in enumerate(valid)}
        h, l, c, v = Ha[valid, k], La[valid, k], Ca[valid, k], Va[valid, k]
        for t in ts:
            j = pos.get(int(t))
            if j is None or j < 120:
                continue
            f = vcp.detect(h[:j], l[:j], c[:j], v[:j])              # 돌파 전날까지의 정보로만 찾는다
            if f is None:
                continue
            cp, P = c[j], f.pivot
            if not (P < cp <= P * (1 + MAX_ABOVE)):
                continue
            if (c[f.pivot_idx + 1:j] > P).any() or (c[f.base_low_idx + 1:j] < f.base_low).any():
                continue                                            # 이미 넘었던 형성이거나 베이스가 무너진 형성
            key = (tk, int(valid[f.base_high_idx]))
            if key in seen:
                continue
            seen.add(key)
            a = vcp.analyze(h[:j + 1], l[:j + 1], c[:j + 1], v[:j + 1], rs=RS[t - 1, k], formation=f)
            if a.state != vcp.BREAKOUT or a.breakout.idx != j:
                continue
            e1 = Oa[t + 1, k]
            rp = min(0.10, max(0.04, 2.0 * ATRP[t - 1, k])) if not np.isnan(ATRP[t - 1, k]) else np.nan
            comp, cons = a.score.components, f.contractions
            vols = [x.avg_volume for x in cons]
            row = {
                "ticker": tk, "date": dates[t], "q": str(dates[t].to_period("Q")), "mkt": mkt[tk], "rs": RS[t - 1, k],
                "r1": sim_m(k, t + 1, e1), "r1n": sim_m(k, t + 1, e1, rp) if not np.isnan(rp) else None, "rp": rp,
                "total": a.score.total, "grade": a.score.grade or "-",
                "n": len(cons), "d1": cons[0].depth_pct, "dlast": cons[-1].depth_pct,
                "last_vs_first": comp["contraction"].detail.get("last_vs_first"),
                "dry": comp["volume"].detail["dry_ratio"],
                "vol_shrink": (vols[-1] / vols[0]) if len(vols) >= 2 and vols[0] and vols[-1] else np.nan,
                "range5": comp["tightness"].detail["range5_pct"], "range10": comp["tightness"].detail["range10_pct"],
                "bvr": a.breakout.volume_ratio, "ext": cp / P - 1, "base_len": j - 1 - f.base_high_idx,
                "trend": comp["trend"].detail.get("ratio"), "atrp": ATRP[t - 1, k],
            }
            row.update({f"p_{name}": comp[name].points for name in vcp.WEIGHTS})
            rows.append(row)
        if (k + 1) % 100 == 0:
            print(f"  {k + 1}/{len(tickers)}종목 {time.time() - t0:.0f}s, 이벤트 {len(rows)}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"V 이벤트 {len(df)}건, 총 {time.time() - t0:.0f}s, 저장 {OUT}", flush=True)
    return df


def qwins(a, b, col):
    wins = used = 0
    for q in sorted(set(a.q) & set(b.q)):
        x, y = a[a.q == q][col].dropna(), b[b.q == q][col].dropna()
        if len(x) >= 5 and len(y) >= 5:
            used += 1
            wins += int(x.mean() > y.mean())
    return wins, used


def compare(name, a, b):
    out = f"{name:<30} n={len(a):>5}/{len(b):>5}"
    for col, lab in COLS:
        x, y = a[col].dropna(), b[col].dropna()
        if len(x) < 5 or len(y) < 5:
            out += f" | {lab} n/a"
            continue
        w, u = qwins(a, b, col)
        diff = x.mean() - y.mean()
        ok = len(x) >= MIN_N and u and w / u >= 0.7 and diff >= 0.05
        out += f" | {lab} {x.mean():+.2f}R vs {y.mean():+.2f}R ({diff:+.2f}) 분기 {w}/{u}{' 채택기준 충족' if ok else ''}"
    print(out)


def spearman(a, b):
    """scipy 없이 순위상관(공용 venv에 설치하지 않는다). 둘 다 값이 있는 쌍만 쓴다."""
    d = pd.DataFrame({"a": a, "b": b}).dropna()
    return d.a.rank().corr(d.b.rank()) if len(d) > 2 else float("nan")


def auc(y, s):
    d = pd.DataFrame({"y": y, "s": s}).dropna()
    pos, neg = (d.y == 1).sum(), (d.y == 0).sum()
    if not pos or not neg:
        return float("nan")
    ranks = d.s.rank()
    return (ranks[d.y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)


def report(v):
    base = pd.read_csv(BASE_CSV, parse_dates=["date"])
    d, b = base[base.kind == "D"], base[base.kind == "B"]
    print("\n=== 1. 기본 성과 (M1a 청산, 평균 R) ===")
    for name, x in (("B 트렌드템플릿 아무 날", b), ("D 40일 고가 돌파", d), ("V 점수 모델 형성 돌파(전체)", v)):
        print(f"  {name:<28} n={len(x):>6}  E1 {x.r1.mean():+.3f}R  E1n {x.r1n.mean():+.3f}R  수익 비율 {(x.r1.dropna() > 0).mean():.0%}")
    elig = v[v.total >= vcp.MIN_SCORE]
    print(f"\n  V 중 자격(60점 이상) {len(elig)}건 ({len(elig) / len(v):.0%})")
    print("\n--- 등급별 ---")
    for name, lo, hi in (("A+ (90~)", 90, 101), ("A (80~90)", 80, 90), ("B (70~80)", 70, 80), ("C (60~70)", 60, 70), ("제외 (~60)", 0, 60)):
        x = v[(v.total >= lo) & (v.total < hi)]
        if len(x):
            print(f"  {name:<12} n={len(x):>5}  E1 {x.r1.mean():+.3f}R  E1n {x.r1n.mean():+.3f}R  수익 비율 {(x.r1.dropna() > 0).mean():.0%}")

    print("\n=== 2. 채택 기준 점검 (앞 / 뒤, 분기별 앞선 비율) ===")
    compare("V 자격 vs V 미만", elig, v[v.total < vcp.MIN_SCORE])
    compare("V 80점+ vs V 80점 미만", v[v.total >= 80], v[v.total < 80])
    compare("V 70점+ vs V 70점 미만", v[v.total >= 70], v[v.total < 70])
    compare("V 자격 vs D 40일 고가 돌파", elig, d)
    compare("V 자격 vs B 아무 날", elig, b)
    compare("V 80점+ vs D", v[v.total >= 80], d)
    for name, sub in (("2010~2020", v[v.date < "2021-01-01"]), ("2021~2026", v[v.date >= "2021-01-01"])):
        e = sub[sub.total >= vcp.MIN_SCORE]
        print(f"\n--- {name} ---")
        compare("V 자격 vs V 미만", e, sub[sub.total < vcp.MIN_SCORE])
        compare("V 80점+ vs 80점 미만", sub[sub.total >= 80], sub[sub.total < 80])
        dd = d[d.date < "2021-01-01"] if name == "2010~2020" else d[d.date >= "2021-01-01"]
        compare("V 자격 vs D", e, dd)

    print("\n=== 3. 돌파 거래량 등급 (자격 종목 안에서) ===")
    g = pd.cut(elig.bvr, [0, 1.0, 1.2, 1.5, 2.0, 99], labels=["~1.0 약함", "1.0~1.2", "1.2~1.5", "1.5~2.0", "2.0~ 강함"], right=False)
    for lab in g.cat.categories:
        x = elig[g == lab]
        if len(x):
            print(f"  {lab:<10} n={len(x):>5}  E1 {x.r1.mean():+.3f}R  E1n {x.r1n.mean():+.3f}R")
    compare("돌파 거래량 1.5배+ vs 미만", elig[elig.bvr >= 1.5], elig[elig.bvr < 1.5])
    compare("돌파 거래량 1.2배+ vs 미만", elig[elig.bvr >= 1.2], elig[elig.bvr < 1.2])

    print("\n=== 4. 항목 점수와 R의 순위상관 (탐색용. 채택 근거 아님) ===")
    for col in ["total"] + [f"p_{k}" for k in vcp.WEIGHTS] + ["bvr", "dry", "rs"]:
        s1 = spearman(v[col], v.r1)
        s2 = spearman(v[col], v.r1n)
        print(f"  {col:<16} E1 {s1:+.3f}  E1n {s2:+.3f}")
    print("\n=== 5. 점수 합계의 수익 예측력 (R>0 AUC, 기간 분할) ===")
    y = (v.r1 > 0).astype(int)
    for name, m in (("전체", v.date.notna()), ("2010~2018", v.date < "2019-01-01"), ("2019~", v.date >= "2019-01-01")):
        print(f"  [{name}] n={int(m.sum())}  총점 {auc(y[m], v.total[m]):.3f} | RS {auc(y[m], v.rs[m]):.3f} "
              f"| 수축점수 {auc(y[m], v.p_contraction[m]):.3f} | 거래량점수 {auc(y[m], v.p_volume[m]):.3f} | 타이트 {auc(y[m], v.p_tightness[m]):.3f}")


INSTRUCTIONS = (
    "The state describes a stock's consolidation base (Mark Minervini volatility contraction pattern), measured the day "
    "it first closes above its pivot. The stock is bought at the next day's open with an 8% stop, half sold at a +20% gain, "
    "the rest trailed under the 50-day average. Decide: this trade will end with a profit."
)


def jev_state(r):
    s = {
        "contractions": int(r.n),
        "first_pullback_depth_pct": round(r.d1, 1),
        "last_pullback_depth_pct": round(r.dlast, 1),
        "last_to_first_depth_ratio": None if pd.isna(r.last_vs_first) else round(r.last_vs_first, 2),
        "volume_last_10d_to_50d_ratio": None if pd.isna(r.dry) else round(r.dry, 2),
        "contraction_volume_last_to_first_ratio": None if pd.isna(r.vol_shrink) else round(r.vol_shrink, 2),
        "price_range_last_5d_pct": round(r.range5, 1),
        "price_range_last_10d_pct": round(r.range10, 1),
        "breakout_day_volume_to_50d_avg_ratio": None if pd.isna(r.bvr) else round(r.bvr, 2),
        "breakout_close_above_pivot_pct": round(r.ext * 100, 1),
        "base_length_days": int(r.base_len),
        "daily_atr_pct": None if pd.isna(r.atrp) else round(r.atrp * 100, 2),
        "relative_strength_percentile": round(r.rs),
    }
    return {k: x for k, x in s.items() if x is not None}


async def jev_probe(rows):
    from pydantic_ai import Agent
    from pydantic_ai.models.typesafe import TypeSafeModel
    from pydantic_ai.providers.typesafe import TypeSafeProvider

    gw_key = os.getenv("AI_GATEWAY_API_KEY")
    provider = TypeSafeProvider(api_key=gw_key, base_url=GATEWAY_URL) if gw_key else TypeSafeProvider()
    print(f"Jev 경로: {GATEWAY_URL if gw_key else 'TypeSafe 직통'}  모델: {MODEL}", flush=True)
    agent = Agent(TypeSafeModel(MODEL, provider=provider), output_type=bool, instructions=INSTRUCTIONS)
    sem = asyncio.Semaphore(8)

    async def one(r):
        async with sem:
            try:
                res = await agent.run(str(jev_state(r)))
                conf = res.response.provider_details["confidence"]
                c = next(iter(conf.values())) if isinstance(conf, dict) else float(conf)
                return c if res.output else 1 - c
            except Exception as ex:
                print(f"  실패: {ex}", flush=True)
                return None

    return await asyncio.gather(*(one(r) for r in rows))


def jev_report(v, per_period):
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    if not (os.getenv("AI_GATEWAY_API_KEY") or os.getenv("TYPESAFE_API_KEY")):
        print("\nJev API 키 없음. Jev 비교는 건너뜀")
        return
    d = v.dropna(subset=["r1"])
    parts = [d[d.date < "2019-01-01"], d[d.date >= "2019-01-01"]]
    s = pd.concat([p.sample(n=min(per_period, len(p)), random_state=0).assign(period=i) for i, p in enumerate(parts)]).reset_index(drop=True)
    s["y"] = (s.r1 > 0).astype(int)
    print(f"\n=== 6. Jev 표본 {len(s)}건 (기간별 {s.period.value_counts().sort_index().to_dict()}), 수익 비율 {s.y.mean():.2f} ===", flush=True)
    s["p_jev"] = asyncio.run(jev_probe(list(s.itertuples())))
    s.to_csv(OUT_JEV, index=False, encoding="utf-8-sig")
    ok = s.p_jev.notna()
    print(f"응답 {ok.sum()}/{len(s)}건, Jev 출력 표준편차 {s.p_jev[ok].std():.3f} (0에 가까우면 거의 상수)")
    for name, g in (("전체", s[ok]), ("2010~2018", s[ok & (s.period == 0)]), ("2019~", s[ok & (s.period == 1)])):
        print(f"[{name}] n={len(g)}  AUC Jev {auc(g.y, g.p_jev):.3f} | 점수 총점 {auc(g.y, g.total):.3f} | RS {auc(g.y, g.rs):.3f} "
              f"| 돌파거래량 {auc(g.y, g.bvr):.3f} | 순위상관(Jev, 총점) {spearman(g.p_jev, g.total):+.2f}")
    bins = pd.cut(s.p_jev[ok], [0, .4, .5, .6, 1.0], include_lowest=True)
    print("\nJev 확률 구간별 실제 수익 비율, 평균 R:")
    print(s[ok].groupby(bins, observed=True).agg(n=("y", "size"), 수익비율=("y", "mean"), 평균R=("r1", "mean")))
    print(f"\n저장: {OUT_JEV}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-jev", action="store_true", help="Jev 비교를 건너뜀")
    ap.add_argument("--per-period", type=int, default=300, help="Jev에 보낼 기간당 표본 수")
    ap.add_argument("--reuse", action="store_true", help="저장된 이벤트 CSV를 다시 쓰고 이벤트 생성을 건너뜀")
    args = ap.parse_args()
    if args.reuse and os.path.exists(OUT):
        v = pd.read_csv(OUT, parse_dates=["date"])
    else:
        v = build_events()
    report(v)
    if not args.no_jev:
        jev_report(v, args.per_period)


if __name__ == "__main__":
    main()
