# -*- coding: utf-8 -*-
"""VCP 판정 시험: 정답을 아는 데이터로 v1(현행), v3(엄격 시안), v4(점검표 시안)를 비교한다.

  A. 책 사례(GRA 2004) 근사: 세 판정이 VCP로 알아보는가, v4가 읽은 수축이 책의 T1~T3와 맞는가
  B. 왕복: 수축 깊이를 지정해 만든 데이터를 판정에 넣으면 같은 개수와 낙폭이 나오는가
  C. 변형: 정의를 하나씩만 깨뜨린 데이터에서 v4가 정확히 그 항목만 떨어뜨리는가
  D. 잡음 내성: 잡음이 커질 때 세 판정의 검출률

앱과 DB를 건드리지 않는다. 사용: python scripts/research/vcp_synthetic_test.py
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.screener import detect_vcp  # noqa: E402
from vcp_cases import GRA_BOOK_DEPTHS, build_gra, make_vcp  # noqa: E402
from vcp_v3_audit import v3  # noqa: E402
from vcp_v4 import vcp_checks  # noqa: E402


def run_all(df):
    c = df.c.values
    r1 = detect_vcp(pd.Series(c))
    r3 = v3(df.h.values, df.l.values, c)
    r4 = vcp_checks(df.h.values, df.l.values, c)
    return r1, r3, r4


def section_a(n=40):
    print("=== A. 책 사례 GRA 2004 (사진에서 읽은 값을 근사로 다시 그린 것, 돌파 전날까지) ===")
    for wicks in (True, False):
        res = [run_all(build_gra(seed=s, wicks=wicks)) for s in range(n)]
        p1 = np.mean([r[0]["detected"] for r in res])
        p3 = np.mean([r[1]["ok"] for r in res])
        p4 = np.mean([r[2]["ok"] for r in res])
        depths = np.array([r[2]["depths"][:3] for r in res if len(r[2].get("depths", [])) >= 3])
        med = np.median(depths, axis=0) if len(depths) else None
        print(f"  꼬리 {'있음' if wicks else '없음'}: VCP로 판정한 비율 v1 {p1:.0%} | v3 {p3:.0%} | v4 {p4:.0%}"
              + (f"   v4가 읽은 T1~T3 중앙값 {np.round(med, 1).tolist()} (책 {list(GRA_BOOK_DEPTHS)})" if med is not None else ""))
        fails = {}
        for r in res:
            for f in r[2].get("failed", []):
                fails[f] = fails.get(f, 0) + 1
        if fails:
            print("     v4가 떨어뜨린 항목:", fails)
        if wicks:
            v1d = [r[0].get("correction_pcts") for r in res[:3]]
            print("     v1이 읽은 수축 예(앞 3회):", v1d)


def section_b(seeds=10):
    print("\n=== B. 왕복: 지정한 깊이를 판정이 되돌려주는가 (v4, 낙폭 오차 2.5%p 이내) ===")
    for depths in ([25, 12, 6], [30, 15, 8, 4], [20, 10], [35, 18, 9, 5], [20, 12, 8, 5]):
        for noise in (0.002, 0.005, 0.010):
            ok = cnt = 0
            for s in range(seeds):
                r = vcp_checks(*[make_vcp(depths, noise=noise, seed=s)[k].values for k in ("h", "l", "c")])
                got = r.get("depths", [])
                if len(got) == len(depths):
                    cnt += 1
                    if all(abs(g - d) <= 2.5 for g, d in zip(got, depths)):
                        ok += 1
            print(f"  깊이 {depths} 잡음 {noise:.1%}: 개수 일치 {cnt}/{seeds}, 낙폭까지 일치 {ok}/{seeds}")


MUTATIONS = {
    "정상(기준)":            (dict(depths=[20, 10, 5]), set()),
    # 고점이 거의 같은데 마지막 조정이 더 깊으면 저점도 내려가므로 두 항목이 함께 떨어지는 게 정상
    "다시 벌어짐":           (dict(depths=[32, 7, 11]), {"점점 얕아짐", "저점 상승"}),
    "첫 수축이 너무 깊음":   (dict(depths=[45, 20, 8]), {"첫 수축 깊이"}),
    "수축이 하나뿐":         (dict(depths=[11], legs=(9, 12)), {"수축 개수", "마지막/첫 수축"}),
    "마지막 수축이 큼":      (dict(depths=[32, 25, 17]), {"마지막 수축 크기"}),
    "베이스가 너무 짧음":    (dict(depths=[20, 10, 5], legs=(1, 2)), {"베이스 길이"}),
    # 피벗이 베이스 고점보다 충분히 낮아야, 피벗 위 7%에 있어도 베이스 고점은 못 넘는다
    "피벗 위로 이미 연장":   (dict(depths=[20, 10, 5], highs=[1, 0.90, 0.88], final=1.07), {"피벗 대비 위치"}),
    "고점에서 멀어짐":       (dict(depths=[20, 10, 4], highs_drop=0.07), {"고점 근처"}),
    # 블로그(책스토리 2편)의 구분: 비슷한 크기로 계속 흔들리면 VCP가 아니라 박스권
    "박스권(크기 비슷)":     (dict(depths=[15, 14, 16, 13]), {"마지막/첫 수축"}),
}


def section_c(seeds=10):
    print("\n=== C. 변형: 정의를 하나씩 깨뜨리면 그 항목만 떨어지는가 (v4) ===")
    for name, (kw, expect) in MUTATIONS.items():
        exact = 0
        seen = {}
        for s in range(seeds):
            r = vcp_checks(*[make_vcp(noise=0.003, seed=s, **kw)[k].values for k in ("h", "l", "c")])
            got = set(r.get("failed", []))
            exact += got == expect
            for f in got:
                seen[f] = seen.get(f, 0) + 1
        print(f"  {name:<14} 기대 {sorted(expect) or '모두 통과'} | 정확히 일치 {exact}/{seeds} | 실제 탈락 {seen or '없음'}")


def section_d(seeds=20):
    print("\n=== D. 잡음 내성: 정상 VCP의 검출률 (v1 / v3 / v4) ===")
    for depths in ([25, 12, 6], [30, 15, 8, 4]):
        for noise in (0.0, 0.003, 0.006, 0.010, 0.015):
            hit = np.zeros(3)
            for s in range(seeds):
                r = run_all(make_vcp(depths, noise=noise, seed=s))
                hit += [r[0]["detected"], r[1]["ok"], r[2]["ok"]]
            a, b, c = (hit / seeds)
            print(f"  깊이 {depths} 잡음 {noise:.1%}: v1 {a:.0%} | v3 {b:.0%} | v4 {c:.0%}")


def _live_arrays():
    import sqlite3
    c = sqlite3.connect(f"file:{os.path.join(ROOT, 'screener.db')}?mode=ro", uri=True)
    d = c.execute("select max(screen_date) from screening_results").fetchone()[0]
    rows = c.execute("select s.id, s.ticker, r.technical_pass from screening_results r join stocks s on s.id = r.stock_id "
                     "where r.screen_date = ?", (d,)).fetchall()
    out = []
    for sid, tk, tech in rows:
        px = pd.read_sql("select high, low, close, volume from daily_prices where stock_id=? order by date", c, params=(sid,)).dropna(subset=["close"])
        if len(px) >= 120:
            out.append((tk, bool(tech), px.high.values, px.low.values, px.close.values, px.volume.values))
    return out


def section_e(n=40):
    import itertools
    import vcp_v4
    print("\n=== E. 임계값 민감도: GRA 통과율과 오늘 실제 종목에서 걸리는 개수 (정답 라벨이 없어 개수는 느슨함의 대리 지표) ===")
    live = _live_arrays()
    gra = {w: [build_gra(seed=s, wicks=w) for s in range(n)] for w in (True, False)}
    base = (vcp_v4.SHRINK_TOL, vcp_v4.LAST_MAX, vcp_v4.LOW_TOL)
    print(f"  {'다음/이전':>8} {'마지막':>6} {'저점':>5} | GRA 꼬리있음 | GRA 꼬리없음 | 오늘 추세통과 | 오늘 전체")
    for st, lm, lt in itertools.product((1.15, 1.25, 1.35), (0.12, 0.14, 0.16), (0.98, 0.96)):
        vcp_v4.SHRINK_TOL, vcp_v4.LAST_MAX, vcp_v4.LOW_TOL = st, lm, lt
        g = {w: np.mean([vcp_checks(d.h.values, d.l.values, d.c.values)["ok"] for d in gra[w]]) for w in (True, False)}
        hits = [(tk, tech) for tk, tech, h, l, c, v in live if vcp_checks(h, l, c, v)["ok"]]
        print(f"  {st:>8} {lm:>6.0%} {lt:>5} | {g[True]:>11.0%} | {g[False]:>11.0%} | {sum(t for _, t in hits):>12} | {len(hits):>8}")
    vcp_v4.SHRINK_TOL, vcp_v4.LAST_MAX, vcp_v4.LOW_TOL = base


if __name__ == "__main__":
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
