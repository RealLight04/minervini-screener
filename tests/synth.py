# -*- coding: utf-8 -*-
"""VCP 시험용 합성 데이터. 정답(수축 깊이, 거래량 흐름, 돌파 모양)을 알고 만든다.

make_vcp   수축 깊이를 지정하고 거래량과 돌파 이후 구간까지 붙일 수 있는 생성기
build_gra  미너비니 책 그림 10.5의 GRA 2004 사례를 사진에서 읽은 값으로 근사한 것(거래량 없음)
"""
import numpy as np
import pandas as pd

GRA_POINTS = [("2004-01-02", 3.3), ("2004-03-01", 3.0), ("2004-05-20", 2.45), ("2004-06-01", 3.0),
              ("2004-06-08", 4.6), ("2004-06-14", 4.7), ("2004-06-18", 5.2), ("2004-06-28", 6.7),
              ("2004-07-01", 6.0), ("2004-07-07", 5.85), ("2004-07-13", 6.3), ("2004-07-23", 5.3),
              ("2004-07-27", 5.5), ("2004-08-04", 6.1), ("2004-08-09", 5.6), ("2004-08-13", 6.15),
              ("2004-08-19", 5.85), ("2004-08-25", 6.05)]
GRA_WICKS = (("2004-07-01", 5.4), ("2004-07-23", 4.8), ("2004-08-19", 5.5))
GRA_BOOK_DEPTHS = (29, 11, 10)   # 사진에서 읽은 T1, T2, T3 낙폭(%, 꼬리 기준 근사)


def build_gra(seed=0, level_jitter=0.01, noise=0.004):
    """GRA 2004-06-28 고점부터 돌파 전날(08-25)까지. 2004-06-28 이전은 모양만 비슷한 가짜 이력이다."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2004-01-02", "2004-08-25")
    pts = {pd.Timestamp(d): p * (1 + rng.normal(0, level_jitter) if d >= "2004-06-28" else 1.0) for d, p in GRA_POINTS}
    c = pd.Series(pts).reindex(idx).interpolate(method="time").values
    c = c * (1 + rng.normal(0, noise, len(c)))
    h = c * (1 + np.abs(rng.normal(0.010, 0.004, len(c))))
    l = c * (1 - np.abs(rng.normal(0.010, 0.004, len(c))))
    for d, low in GRA_WICKS:
        l[idx.get_loc(pd.Timestamp(d))] = low * (1 + rng.normal(0, level_jitter))
    h[idx.get_loc(pd.Timestamp("2004-06-28"))] = 6.8 * (1 + rng.normal(0, level_jitter))
    return {"h": h, "l": l, "c": c}


def make_vcp(depths, h1=100.0, highs_drop=0.01, highs=None, noise=0.003, seed=0, legs=(6, 9), coil=12,
             prior_days=230, vol_decay=0.7, dry=0.7, final=0.985, breakout=None, after=()):
    """수축 깊이(%)를 지정해 일봉을 만든다.

    depths     [22, 11, 5] 처럼 T1, T2, T3 낙폭(%). T1은 베이스 고점에서 그 저점까지
    highs_drop 수축마다 시작 고점이 이만큼씩 낮아진다(0.01이면 1%). highs로 직접 줄 수도 있다
    vol_decay  수축이 하나 지날 때마다 그 구간 거래량이 이 배율로 줄어든다(1.0이면 안 줄어듦)
    dry        마지막 타이트 구간(coil) 거래량 = 마지막 수축 거래량 x dry
    final      마지막 상승이 멈추는 위치 / 피벗. 0.985면 피벗 1.5% 아래에서 조용히 다진다
    breakout   {"pct": 0.03, "mult": 1.8}이면 피벗 위 3%로 마감하고 거래량은 직전 50일 평균의 1.8배
    after      돌파 뒤 종가를 피벗 배율로 이어붙인다. (1.04, 1.01, 0.97)이면 돌파 뒤 피벗 아래로 되밀림
    반환: h, l, c, v 배열과 pivot, pre_idx(돌파 전 마지막 봉), breakout_idx(없으면 None)
    """
    rng = np.random.default_rng(seed)
    n_t = len(depths)
    hs = highs if highs is not None else [1 - highs_drop * i for i in range(n_t)]
    H_sw = [h1 * m for m in hs]
    L_sw = [H_sw[i] * (1 - depths[i] / 100) for i in range(n_t)]
    prior = np.linspace(h1 * 0.45, h1, prior_days)
    path, vf, marks = list(prior[:-1]), [1.0] * (prior_days - 1), {}
    marks[len(path)] = ("H", H_sw[0])
    path.append(H_sw[0])
    vf.append(1.0)
    for i in range(n_t):
        f = vol_decay ** i
        down = int(rng.integers(legs[0], legs[1] + 1))
        start = path[-1]
        for k in range(1, down + 1):
            path.append(start + (L_sw[i] - start) * k / down)
            vf.append(f)
        marks[len(path) - 1] = ("L", L_sw[i])
        up = int(rng.integers(legs[0], legs[1] + 1))
        target = H_sw[i + 1] if i + 1 < n_t else H_sw[-1] * final
        start = path[-1]
        for k in range(1, up + 1):
            path.append(start + (target - start) * k / up)
            vf.append(f * 0.9)
        if i + 1 < n_t:
            marks[len(path) - 1] = ("H", target)
    base = path[-1]
    for k in range(coil):
        path.append(base * (1 + 0.004 * np.sin(k * 1.7)))
        vf.append(vol_decay ** (n_t - 1) * dry)
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
    v = 1e6 * np.array(vf) * (1 + rng.normal(0, 0.08, len(vf)))
    pivot = H_sw[-1]
    pre_idx = len(p) - 1
    h, l, c, v = list(h), list(l), list(c), list(v)
    b_idx = None
    if breakout is not None:
        avg = float(np.mean(v[-50:]))
        cb = pivot * (1 + breakout["pct"])
        b_idx = len(c)
        c.append(cb)
        h.append(cb * 1.005)
        l.append(c[-2] * 0.998)
        v.append(avg * breakout["mult"])
    for m in after:
        cb = pivot * m
        c.append(cb)
        h.append(cb * 1.004)
        l.append(cb * 0.996)
        v.append(float(np.mean(v[-50:])))
    return {"h": np.array(h), "l": np.array(l), "c": np.array(c), "v": np.array(v),
            "pivot": pivot, "pre_idx": pre_idx, "breakout_idx": b_idx}


def trim(d, n):
    """앞에서 n개 봉만 남긴다(돌파 전날까지 자르는 용도)."""
    return {k: (x[:n] if isinstance(x, np.ndarray) else x) for k, x in d.items()}
