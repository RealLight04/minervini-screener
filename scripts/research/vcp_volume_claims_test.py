# -*- coding: utf-8 -*-
"""'베이스에서 거래량이 마르고, 돌파일에 거래량이 늘면 좋다'는 주장을 2010~2026 이벤트로 시험한다.

이벤트: vcp_v2_detector_test.py가 만든 D 이벤트(트렌드 템플릿 + RS 70 이상 종목이 직전 40일 고가를 종가로 처음
넘은 날, 5% 이내). 종가로 확인한 이벤트라 돌파일 거래량을 그날 장 마감 뒤에 알 수 있고, 진입은 다음 날 시가(E1).
청산은 제품 관리(M1a). 정확한 R은 고정 -8% 손절(r1)과 ATR 정규화(r1n) 두 가지로 본다.

  dry   = 돌파 전 10거래일 평균 거래량 / 50일 평균 거래량 (작을수록 마름. 사이트 기준 0.85 이하)
  surge = 돌파일 거래량 / 직전 50일 평균 거래량

채택 기준(결과 전에 정함): 쪼갠 쪽 표본 150 이상, 분기의 70% 이상에서 반대쪽보다 앞서고, 평균 차이 +0.05R 이상.
선행: vcp_v2_detector_test.py 실행(CSV 생성)
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from vcp_v2_detector_test import line  # noqa: E402

COLS = (("r1", "E1"), ("r1n", "E1n"))


def main():
    ev = pd.read_csv(os.path.join(HERE, "data", "vcp_v2_detector_test.csv"), parse_dates=["date"])
    d = ev[ev.kind == "D"].dropna(subset=["r1", "r1n"]).copy()
    con = sqlite3.connect(os.path.join(HERE, "data", "prices.sqlite"))
    px = pd.read_sql("SELECT ticker, date, volume FROM prices", con, parse_dates=["date"])
    con.close()
    V = px.pivot(index="date", columns="ticker", values="volume").sort_index().ffill()
    prior10 = V.shift(1).rolling(10).mean()
    prior50 = V.shift(1).rolling(50).mean()
    dry_t = (prior10 / prior50)
    sur_t = V / prior50
    d["dry"] = [dry_t.at[dt, tk] if dt in dry_t.index else np.nan for tk, dt in zip(d.ticker, d.date)]
    d["surge"] = [sur_t.at[dt, tk] if dt in sur_t.index else np.nan for tk, dt in zip(d.ticker, d.date)]
    d = d.dropna(subset=["dry", "surge"])
    print(f"D 이벤트 {len(d)}건 (2010~2026), 평균 R E1 {d.r1.mean():+.3f}, E1n {d.r1n.mean():+.3f}")

    def table(col, bins, labels, title):
        print(f"\n--- {title} ---")
        g = pd.cut(d[col], bins, labels=labels, right=False)
        for lab in labels:
            x = d[g == lab]
            if len(x):
                print(f"  {lab:<10} n={len(x):>5}  E1 {x.r1.mean():+.3f}R  E1n {x.r1n.mean():+.3f}R  수익 비율 {(x.r1 > 0).mean():.0%}")

    table("dry", [0, 0.6, 0.8, 1.0, 99], ["~0.6", "0.6~0.8", "0.8~1.0", "1.0~"], "돌파 전 거래량 마름(dry): 작을수록 마름")
    table("surge", [0, 1.0, 1.4, 2.0, 99], ["~1.0", "1.0~1.4", "1.4~2.0", "2.0~"], "돌파일 거래량(surge): 클수록 급증")

    print("\n=== 채택 기준 점검 (쪼갠 쪽 vs 나머지, 전체 기간) ===")
    line("마름(dry<=0.85)", d, d.dry <= 0.85, COLS)
    line("급증(surge>=1.4)", d, d.surge >= 1.4, COLS)
    line("급증(surge>=2.0)", d, d.surge >= 2.0, COLS)
    ideal = (d.dry <= 0.85) & (d.surge >= 1.4)
    line("마름 and 급증(이상형)", d, ideal, COLS)
    line("마름 and 급증 없음", d, (d.dry <= 0.85) & (d.surge < 1.4), COLS)
    line("마르지 않음 and 급증", d, (d.dry > 0.85) & (d.surge >= 1.4), COLS)
    for name, sub in (("2010~2020", d[d.date < "2021-01-01"]), ("2021~2026", d[d.date >= "2021-01-01"])):
        print(f"\n--- {name} ---")
        line("마름(dry<=0.85)", sub, sub.dry <= 0.85, COLS)
        line("급증(surge>=1.4)", sub, sub.surge >= 1.4, COLS)
        line("마름 and 급증(이상형)", sub, (sub.dry <= 0.85) & (sub.surge >= 1.4), COLS)
    print("\n--- 국면별 (이상형 vs 나머지) ---")
    for r in ("BULL", "NEUTRAL"):
        sub = d[d.regime == r]
        line(f"{r} 이상형", sub, (sub.dry <= 0.85) & (sub.surge >= 1.4), COLS)


if __name__ == "__main__":
    main()
