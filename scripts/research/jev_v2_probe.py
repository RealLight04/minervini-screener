# -*- coding: utf-8 -*-
"""Jev(TypeSafe AI)에게 v2 베이스 형태 수치만 보여주고 돌파 매수가 수익으로 끝날지 묻는다.

vcp_v2_detector_test.csv의 DI 이벤트(장중 돌파 매수)를 쓴다. 보내는 값은 돌파 전날까지의 형태 수치뿐이고
티커, 날짜, 거래량 확인, 결과는 보내지 않는다. 정답은 청산 관리(M1a) 결과 R > 0.
기간을 둘로 나눠 표본을 뽑아(2010~2018, 2019~) 기간별 AUC와 단순 지표(RS, 수축 수 등)의 AUC를 나란히 본다.
DB는 읽지 않고 프로덕션에 아무것도 반영하지 않는다.

선행: scripts/research/vcp_v2_detector_test.py 로 CSV 생성, .env 에 TYPESAFE_API_KEY
사용: python scripts/research/jev_v2_probe.py [--per-period N]
출력: scripts/research/data/jev_v2_probe.csv
"""
import argparse
import asyncio
import os
import sys

import pandas as pd
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SRC = os.path.join(DATA, "vcp_v2_detector_test.csv")
OUT = os.path.join(DATA, "jev_v2_probe.csv")
MODEL = os.getenv("JEV_MODEL", "jev-1.13.0")
GATEWAY_URL = os.getenv("JEV_GATEWAY_URL", "https://ai-gateway.vercel.sh/typesafe")
CONCURRENCY = 8

INSTRUCTIONS = (
    "The state describes a stock's consolidation base (Mark Minervini volatility contraction pattern) "
    "the day before it first trades above its pivot. The stock is bought at the pivot with an 8% stop, "
    "half sold at a +20% gain, the rest trailed under the 50-day average. "
    "Decide: this trade will end with a profit."
)


def state_of(r):
    s = {
        "contractions": int(r.n),
        "first_pullback_depth_pct": round(r.d1 * 100, 1),
        "last_pullback_depth_pct": round(r.last * 100, 1),
        "last_to_first_depth_ratio": round(r.last / r.d1, 2) if r.d1 else None,
        "price_range_last_10d_pct": round(r.rng10 * 100, 1),
        "atr10_to_atr50_ratio": round(r.atr_ratio, 2),
        "daily_atr_pct": round(r.atrp * 100, 2),
        "relative_strength_percentile": round(r.rs),
    }
    return {k: v for k, v in s.items() if v is not None and not (isinstance(v, float) and pd.isna(v))}


def auc(y, s):
    d = pd.DataFrame({"y": y, "s": s}).dropna()
    pos, neg = (d.y == 1).sum(), (d.y == 0).sum()
    if not pos or not neg:
        return float("nan")
    ranks = d.s.rank()
    return (ranks[d.y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)


async def probe(rows):
    from pydantic_ai import Agent
    from pydantic_ai.models.typesafe import TypeSafeModel
    from pydantic_ai.providers.typesafe import TypeSafeProvider

    gw_key = os.getenv("AI_GATEWAY_API_KEY")
    provider = TypeSafeProvider(api_key=gw_key, base_url=GATEWAY_URL) if gw_key else TypeSafeProvider()
    print(f"경로: {GATEWAY_URL if gw_key else 'TypeSafe 직통'}  모델: {MODEL}", flush=True)
    agent = Agent(TypeSafeModel(MODEL, provider=provider), output_type=bool, instructions=INSTRUCTIONS)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(r):
        async with sem:
            try:
                res = await agent.run(str(state_of(r)))
                conf = res.response.provider_details["confidence"]
                c = next(iter(conf.values())) if isinstance(conf, dict) else float(conf)
                return c if res.output else 1 - c
            except Exception as ex:
                print(f"  실패: {ex}", flush=True)
                return None

    return await asyncio.gather(*(one(r) for r in rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-period", type=int, default=300, help="기간당 표본 수")
    args = ap.parse_args()
    load_dotenv(os.path.join(ROOT, ".env"))
    if not (os.getenv("AI_GATEWAY_API_KEY") or os.getenv("TYPESAFE_API_KEY")):
        print("API 키 없음")
        return

    df = pd.read_csv(SRC, parse_dates=["date"])
    d = df[df.kind == "DI"].dropna(subset=["r2", "n", "last", "d1", "rng10", "atr_ratio", "atrp", "rs"])
    d = d[d.n >= 1]
    parts = [d[d.date < "2019-01-01"], d[d.date >= "2019-01-01"]]
    s = pd.concat([p.sample(n=min(args.per_period, len(p)), random_state=0).assign(period=i)
                   for i, p in enumerate(parts)]).reset_index(drop=True)
    s["y"] = (s.r2 > 0).astype(int)
    print(f"표본 {len(s)}건 (기간별 {s.period.value_counts().sort_index().to_dict()}), 수익 비율 {s.y.mean():.2f}", flush=True)

    s["p_jev"] = asyncio.run(probe(list(s.itertuples())))
    s.to_csv(OUT, index=False, encoding="utf-8-sig")

    ok = s.p_jev.notna()
    print(f"\n응답 {ok.sum()}/{len(s)}건")
    for name, g in (("전체", s[ok]), ("2010~2018", s[ok & (s.period == 0)]), ("2019~", s[ok & (s.period == 1)])):
        print(f"[{name}] n={len(g)}  AUC Jev {auc(g.y, g.p_jev):.3f} | RS {auc(g.y, g.rs):.3f} "
              f"| 수축수 {auc(g.y, g.n):.3f} | 마지막낙폭(작을수록) {auc(g.y, -g['last']):.3f} | 10일변동폭(작을수록) {auc(g.y, -g.rng10):.3f}")
    bins = pd.cut(s.p_jev[ok], [0, .4, .5, .6, 1.0], include_lowest=True)
    print("\nJev 확률 구간별 실제 수익 비율, 평균 R:")
    print(s[ok].groupby(bins, observed=True).agg(n=("y", "size"), 수익비율=("y", "mean"), 평균R=("r2", "mean")))
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    sys.exit(main())
