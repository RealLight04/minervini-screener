# -*- coding: utf-8 -*-
"""Jev(TypeSafe AI) VCP 돌파 확률 탐색 — 기존 quality 점수와 비교.

vcp_events에서 결과가 확정된 베이스(broke_out / failed)를 읽어, 베이스 형태 수치만 Jev에
보내 "피벗 돌파 성공" 확률을 받고, 실제 결과 대비 AUC를 기존 quality와 나란히 출력한다.
DB는 읽기 전용으로 연다. 프로덕션 스크리닝에는 아무것도 반영하지 않는다.

누수 방지: 티커·종목명·날짜·종가·돌파 필드는 보내지 않는다(모델의 사후 지식·결과 유출 차단).
단 vcp_events 스냅샷은 last_detected 시점 값이라 완전한 시점복원은 아니다 — quality도 같은
조건이므로 비교는 공정하지만, 절대 수치는 낙관적일 수 있다.

선행: pip install "pydantic-ai-slim[typesafe]"  /  .env 에 AI_GATEWAY_API_KEY(Vercel, 우선) 또는 TYPESAFE_API_KEY
      게이트웨이가 모델명을 다르게 받으면 JEV_MODEL=typesafe-ai/jev 로 덮어쓴다.
사용: python scripts/research/jev_vcp_probe.py [--limit N]
출력: scripts/research/data/jev_vcp_probe.csv
"""
import argparse
import asyncio
import os
import sqlite3
import sys

import pandas as pd
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = os.path.join(ROOT, "screener.db")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "jev_vcp_probe.csv")
MODEL = os.getenv("JEV_MODEL", "jev-1.13.0")  # 버전 고정 — jev-latest는 움직이는 별칭이라 재현성이 깨진다
# TypeSafe 신규 가입 중단(2026-09-22) 대비 Vercel AI Gateway 경유. 이 주소는 서드파티 가이드 기준(공식 문서 미확인).
GATEWAY_URL = os.getenv("JEV_GATEWAY_URL", "https://ai-gateway.vercel.sh/typesafe")
CONCURRENCY = 8

INSTRUCTIONS = (
    "The state describes a stock chart base (Mark Minervini volatility contraction pattern) "
    "at the time it was detected. Decide: this base will break out above its pivot price "
    "rather than fail by undercutting its base low."
)

QUERY = """
SELECT e.id, e.status, e.quality, e.contractions, e.last_contraction, e.dryup_ratio,
       e.ud_volume_ratio, e.volume_dryup, e.rs_rank, e.base_seq,
       e.pivot_price, e.stop_loss, e.base_low, e.base_high,
       julianday(e.last_detected) - julianday(e.first_detected) AS base_days
FROM vcp_events e
WHERE e.status IN ('broke_out', 'failed')
"""


def state_of(r):
    def pct(a, b):
        return round((a - b) / a * 100, 2) if a and b else None
    s = {
        "contractions": r.contractions,
        "last_contraction_pct": r.last_contraction,
        "base_depth_pct": pct(r.base_high, r.base_low),
        "stop_distance_pct": pct(r.pivot_price, r.stop_loss),
        "base_length_days": int(r.base_days) if pd.notna(r.base_days) else None,
        "base_number": r.base_seq,
        "volume_dryup": bool(r.volume_dryup),
        "recent10_to_50d_volume_ratio": r.dryup_ratio,
        "up_down_volume_ratio_50d": r.ud_volume_ratio,
        "relative_strength_percentile": r.rs_rank,
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
                return c if res.output else 1 - c  # 선택한 답의 신뢰도 → P(돌파)로 변환
            except Exception as ex:
                print(f"  event {r.id} 실패: {ex}", flush=True)
                return None

    return await asyncio.gather(*(one(r) for r in rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="앞 N건만 (비용·동작 확인용)")
    args = ap.parse_args()

    load_dotenv(os.path.join(ROOT, ".env"))
    if not (os.getenv("AI_GATEWAY_API_KEY") or os.getenv("TYPESAFE_API_KEY")):
        print("API 키 없음 — .env 에 AI_GATEWAY_API_KEY(Vercel) 또는 TYPESAFE_API_KEY 추가")
        return

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.read_sql(QUERY, con)
    con.close()
    if args.limit:
        df = df.sample(n=min(args.limit, len(df)), random_state=0)
    print(f"표본: {len(df)}건 (돌파 {(df.status == 'broke_out').sum()} / 실패 {(df.status == 'failed').sum()})", flush=True)

    df["p_jev"] = asyncio.run(probe(list(df.itertuples())))
    df["y"] = (df.status == "broke_out").astype(int)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")

    ok = df.p_jev.notna()
    print(f"\n응답 {ok.sum()}/{len(df)}건")
    print(f"AUC  Jev     : {auc(df.y[ok], df.p_jev[ok]):.3f}")
    print(f"AUC  quality : {auc(df.y[ok], df.quality[ok]):.3f}   (0.5 = 무작위)")
    print("\nJev 확률 구간별 실제 돌파율:")
    bins = pd.cut(df.p_jev[ok], [0, .3, .5, .7, 1.0], include_lowest=True)
    print(df[ok].groupby(bins, observed=True).y.agg(["count", "mean"]).rename(columns={"mean": "돌파율"}))
    print(f"\n저장: {OUT}")


if __name__ == "__main__":
    sys.exit(main())
