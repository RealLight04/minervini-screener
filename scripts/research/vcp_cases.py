# -*- coding: utf-8 -*-
"""VCP 판정 시험용 데이터: 책 사례(GRA 2004)를 근사로 다시 그린 것과, 수축 깊이를 지정해 만드는 합성 생성기.

GRA는 상장폐지라 가격 데이터가 없어서 책 그림 10.5 사진에서 눈금으로 읽은 스윙(가격 ±0.1달러, 날짜 ±2일)을
이어 만든 근사다. 사진 해석 오차를 흉내 내려고 시드마다 수준을 조금씩 흔든다.
"""
import numpy as np
import pandas as pd

# (날짜, 종가 근사). 2004-06-28 이전은 트렌드 맥락용 모양만 비슷하게 만든 가짜 이력.
GRA_POINTS = [("2004-01-02", 3.3), ("2004-03-01", 3.0), ("2004-05-20", 2.45), ("2004-06-01", 3.0),
              ("2004-06-08", 4.6), ("2004-06-14", 4.7), ("2004-06-18", 5.2), ("2004-06-28", 6.7),
              ("2004-07-01", 6.0), ("2004-07-07", 5.85), ("2004-07-13", 6.3), ("2004-07-23", 5.3),
              ("2004-07-27", 5.5), ("2004-08-04", 6.1), ("2004-08-09", 5.6), ("2004-08-13", 6.15),
              ("2004-08-19", 5.85), ("2004-08-25", 6.05)]
GRA_WICKS = (("2004-07-01", 5.4), ("2004-07-23", 4.8), ("2004-08-19", 5.5))   # 사진에서 보이는 긴 꼬리 저점
GRA_BOOK_DEPTHS = (29, 11, 10)   # 사진에서 읽은 T1, T2, T3 낙폭(%, 꼬리 기준 근사)


def build_gra(seed=0, wicks=True, level_jitter=0.01, noise=0.004):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2004-01-02", "2004-08-25")
    pts = {}
    for d, p in GRA_POINTS:
        j = 1 + rng.normal(0, level_jitter) if d >= "2004-06-28" else 1.0
        pts[pd.Timestamp(d)] = p * j
    c = pd.Series(pts).reindex(idx).interpolate(method="time").values
    c = c * (1 + rng.normal(0, noise, len(c)))
    h = c * (1 + np.abs(rng.normal(0.010, 0.004, len(c))))
    l = c * (1 - np.abs(rng.normal(0.010, 0.004, len(c))))
    if wicks:
        for d, low in GRA_WICKS:
            l[idx.get_loc(pd.Timestamp(d))] = low * (1 + rng.normal(0, level_jitter))
        h[idx.get_loc(pd.Timestamp("2004-06-28"))] = 6.8 * (1 + rng.normal(0, level_jitter))
    return pd.DataFrame({"h": h, "l": l, "c": c}, index=idx)


def make_vcp(depths, h1=100.0, noise=0.004, seed=0, legs=(5, 9), highs_drop=0.01, final=0.98, highs=None):
    """수축 깊이(%)를 지정해 일봉 H/L/C를 만든다. 끝 종가는 마지막 수축 시작 고점(피벗)의 final배.

    depths     [25, 12, 6] 처럼 T1, T2, T3 낙폭(%)
    highs_drop 수축마다 고점이 이만큼씩 낮아짐(0.01이면 1%)
    highs      각 수축 시작 고점을 직접 지정할 때(h1 대비 배율 목록)
    final      마지막 종가 / 피벗
    """
    rng = np.random.default_rng(seed)
    n_t = len(depths)
    hs = highs if highs is not None else [1 - highs_drop * i for i in range(n_t)]
    H_sw = [h1 * m for m in hs]                       # 각 수축을 시작하는 고점
    L_sw = [H_sw[i] * (1 - depths[i] / 100) for i in range(n_t)]
    prior = np.linspace(h1 * 0.45, h1, 220)           # 베이스 전 상승 이력
    path, marks = list(prior[:-1]), {}
    marks[len(path)] = ("H", H_sw[0])
    path.append(H_sw[0])
    for i in range(n_t):
        down = int(rng.integers(legs[0], legs[1] + 1))
        start = path[-1]
        for k in range(1, down + 1):
            path.append(start + (L_sw[i] - start) * k / down)
        marks[len(path) - 1] = ("L", L_sw[i])
        up = int(rng.integers(legs[0], legs[1] + 1))
        target = H_sw[i + 1] if i + 1 < n_t else H_sw[-1] * final
        start = path[-1]
        for k in range(1, up + 1):
            path.append(start + (target - start) * k / up)
        if i + 1 < n_t:
            marks[len(path) - 1] = ("H", target)
    p = np.array(path)
    c = p * (1 + rng.normal(0, noise, len(p)))
    h = np.maximum(c, p) * (1 + np.abs(rng.normal(0, noise / 2, len(p))))
    l = np.minimum(c, p) * (1 - np.abs(rng.normal(0, noise / 2, len(p))))
    for i, (kind, price) in marks.items():
        if kind == "H":
            h[i] = max(h[i], price)
            c[i] = min(c[i], price)
        else:
            l[i] = min(l[i], price)
            c[i] = max(c[i], price)
    idx = pd.bdate_range("2003-01-02", periods=len(p))
    return pd.DataFrame({"h": h, "l": l, "c": c}, index=idx)
