# -*- coding: utf-8 -*-
"""엄격한 VCP 판정(v3) 시안을 오늘 스크리닝 데이터에 돌려 현행 판정(v1)과 비교한다.

정답 라벨이 없으므로 '미너비니 정의와 얼마나 맞는지'를 항목별로 센다. 결과가 성과로 이어지는지는
따로 검증해야 하며, 이 스크립트는 판정 규칙이 정의에 충실한지만 본다. DB는 읽기 전용.

v3 규칙 (베이스는 최근 150거래일에서 가장 높은 확정 고점부터)
  1) 베이스 길이 15거래일 이상
  2) 수축 2~6번, 첫 수축 5% 이상(잡음 제외), 가장 깊은 수축 35% 이하
  3) 조정폭이 뒤로 갈수록 얕아짐: 다음 수축 <= 이전 수축 x 1.15, 마지막 <= 첫 수축의 60%
  4) 저점이 높아짐: 다음 저점 >= 이전 저점 x 0.98
  5) 지금 고점 근처: 종가가 베이스 고점의 90% 이상이고 피벗(마지막 수축 시작 고점) 5% 위 이하
  6) 지금 조용함: 직전 10일 변동폭 10% 이하
선행 조건: 트렌드 템플릿 통과(홈 후보와 같은 기준)

사용: python scripts/research/vcp_v3_audit.py
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from app.screener import detect_vcp  # noqa: E402

DB = os.path.join(ROOT, "screener.db")
THETA_MIN, THETA_ATR, WINDOW = 0.03, 1.5, 150


def zigzag_state(H, L, C):
    """장중 고저 지그재그(인과적). ATR 14일 평균 기준 적응형 임계."""
    n = len(C)
    tr = np.maximum.reduce([H - L, np.abs(H - np.r_[C[0], C[:-1]]), np.abs(L - np.r_[C[0], C[:-1]])])
    atrp = pd.Series(tr).rolling(14).mean().values / C
    swings, d, ext, ext_idx = [], "up", H[0], 0
    for t in range(1, n):
        th = max(THETA_MIN, THETA_ATR * (atrp[t - 1] if not np.isnan(atrp[t - 1]) else 0.0))
        if d == "up":
            if H[t] > ext:
                ext, ext_idx = H[t], t
            elif L[t] <= ext * (1 - th):
                swings.append((ext_idx, ext, "H"))
                d, ext, ext_idx = "down", L[t], t
        else:
            if L[t] < ext:
                ext, ext_idx = L[t], t
            elif H[t] >= ext * (1 + th):
                swings.append((ext_idx, ext, "L"))
                d, ext, ext_idx = "up", H[t], t
    return swings, d, ext, ext_idx


def v3(H, L, C):
    n = len(C)
    swings, d, ext, _ = zigzag_state(H, L, C)
    recent = [s for s in swings if s[0] >= n - 1 - WINDOW]
    while recent and recent[0][2] != "H":
        recent.pop(0)
    pbs = []
    for i in range(0, len(recent) - 1, 2):
        if recent[i][2] == "H" and recent[i + 1][2] == "L":
            pbs.append((recent[i][0], recent[i][1], recent[i + 1][1]))
    if d == "down" and recent and recent[-1][2] == "H":
        pbs.append((recent[-1][0], recent[-1][1], ext))
    if not pbs:
        return {"ok": False, "why": "수축 없음"}
    i0 = max(range(len(pbs)), key=lambda i: (pbs[i][1], -i))
    pbs = pbs[i0:]
    depths = [(h - l) / h for _, h, l in pbs]
    lows = [l for _, _, l in pbs]
    base_high, pivot = pbs[0][1], pbs[-1][1]
    base_len = n - 1 - pbs[0][0]
    close = C[-1]
    rng10 = (H[-10:].max() - L[-10:].min()) / H[-10:].max()
    checks = {
        "베이스 15일+": base_len >= 15,
        "수축 2~6번": 2 <= len(depths) <= 6,
        "첫 수축 5%+": depths[0] >= 0.05,
        "깊이 35% 이하": max(depths) <= 0.35,
        "점점 얕아짐": all(depths[i + 1] <= depths[i] * 1.15 for i in range(len(depths) - 1)),
        "마지막<=첫의 60%": len(depths) >= 2 and depths[-1] <= depths[0] * 0.6,
        "저점 상승": all(lows[i + 1] >= lows[i] * 0.98 for i in range(len(lows) - 1)),
        "고점 근처 90%+": close >= base_high * 0.90,
        "피벗 5% 위 이하": close <= pivot * 1.05,
        "10일 변동 10% 이하": rng10 <= 0.10,
    }
    failed = [k for k, v in checks.items() if not v]
    return {"ok": not failed, "failed": failed, "depths": [round(x * 100, 1) for x in depths],
            "base_len": base_len, "pivot": pivot, "rng10": round(rng10 * 100, 1),
            "from_high": round((1 - close / base_high) * 100, 1)}


def main():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    d = c.execute("select max(screen_date) from screening_results").fetchone()[0]
    stocks = c.execute(
        "select s.id, s.ticker, r.signal, r.technical_pass, r.vcp_detected from screening_results r "
        "join stocks s on s.id = r.stock_id where r.screen_date = ?", (d,)).fetchall()
    print(f"스크리닝 {d}, 전체 {len(stocks)}종목")
    res = []
    for sid, tk, sig, tech, v1 in stocks:
        px = pd.read_sql("select high, low, close from daily_prices where stock_id=? order by date", c, params=(sid,)).dropna()
        if len(px) < 120:
            continue
        r = v3(px.high.values, px.low.values, px.close.values)
        r.update(tk=tk, sig=sig, tech=bool(tech), v1=bool(v1))
        res.append(r)
    df = pd.DataFrame(res)
    print("\n판정 개수")
    print(f"  v1(현행) VCP: 전체 {df.v1.sum()}, 추세 통과 중 {(df.v1 & df.tech).sum()}")
    print(f"  v3(엄격) VCP: 전체(추세 무시) {df.ok.sum()}, 추세 통과 중 {(df.ok & df.tech).sum()}")
    print(f"  v1과 v3가 겹침(추세 통과 중): {(df.v1 & df.ok & df.tech).sum()}")
    print("\n추세 통과 종목에서 v3가 떨어뜨린 이유 (여러 항목이 동시에 해당할 수 있음)")
    t = df[df.tech & ~df.ok]
    from collections import Counter
    cnt = Counter(x for f in t.failed.dropna() for x in f)
    for k, v in cnt.most_common():
        print(f"  {k}: {v}")
    print(f"\n추세 통과 {df.tech.sum()}종목 중 v3 통과 목록")
    for _, r in df[df.tech & df.ok].iterrows():
        print(f"  {r.tk:<10} 수축 {r.depths} 베이스 {r.base_len}일 고점대비 -{r.from_high}% 10일폭 {r.rng10}% 신호 {r.sig} (v1 {'O' if r.v1 else 'X'})")
    print("\n추세 통과 + v1은 VCP지만 v3는 탈락")
    for _, r in df[df.tech & df.v1 & ~df.ok].iterrows():
        print(f"  {r.tk:<10} 수축 {r.depths} 탈락 {r.failed}")
    print("\n추세 통과 + v3만 VCP (v1은 아님)")
    for _, r in df[df.tech & df.ok & ~df.v1].iterrows():
        print(f"  {r.tk:<10} 수축 {r.depths}")


if __name__ == "__main__":
    main()
