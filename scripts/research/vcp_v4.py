# -*- coding: utf-8 -*-
"""VCP 판정 v4: 점검표 구조 + 사람이 세는 방식의 수축 정의. 연구용(앱에 반영하지 않음).

v1, v3와 다른 점
  수축 정의: 베이스 고점(최근 150거래일 최고가)에서 가장 깊은 저점까지가 T1이다. 그 사이의 반등은 T1 안에
            묶는다(책 그림 10.5 GRA 사례에서 미너비니가 T1을 한 번으로 센 방식). 가장 깊은 저점 이후로는
            ATR 적응형 지그재그로 T2, T3를 센다. 아직 끝나지 않은 마지막 조정도 센다.
  판정 구조: 이 모듈은 참/거짓만 돌려주지 않고 항목별 측정값과 통과 여부(Check)를 돌려준다. 같은 점검표를
            판정, 화면 설명, 시험이 함께 쓴다.

임계값은 미너비니 책 GRA 사례와 2차 자료에서 정한 초기 휴리스틱이다. 라벨 표본으로 조정하기 전에는 확정값이
아니며, 백테스트 성과로 조정하지 않는다(과적합).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

THETA_MIN, THETA_ATR = 0.03, 1.5     # 지그재그 반전 임계 = max(3%, 1.5 x ATR14%)
WINDOW = 150                         # 베이스를 찾는 최근 거래일 수
MIN_BASE_DAYS = 15                   # 베이스 최소 길이(약 3주)
MIN_T, MAX_T = 2, 6                  # 수축 횟수 범위(미너비니: 보통 2~6번)
FIRST_MIN, FIRST_MAX = 0.08, 0.35    # 첫 수축 깊이 범위
SHRINK_TOL = 1.35                    # 다음 수축 <= 이전 수축 x 1.35. 책 GRA 사례의 T3가 T2와 거의 같아(중앙값 1.17) 1.15는 저자 본인 예시도 못 받음
LAST_VS_FIRST = 0.60                 # 마지막 수축 <= 첫 수축의 60%
LAST_MAX = 0.15                      # 마지막 수축 15% 이하. GRA T3는 꼬리 기준 약 10~12%
LOW_TOL = 0.96                       # 다음 저점 >= 이전 저점 x 0.96. T2와 T3 저점이 같은 높이라 사진 읽기 오차(약 2%)를 허용
HIGH_TOL = 1.02                      # 이후 고점은 베이스 고점의 102% 이하
NEAR_HIGH = 0.85                     # 종가가 베이스 고점의 85% 이상
EXT_MAX = 1.05                       # 종가가 피벗 위 5% 이하


@dataclass
class Check:
    name: str
    value: float | None
    limit: str
    passed: bool
    required: bool = True


def _theta(H, L, C):
    pc = np.r_[C[0], C[:-1]]
    tr = np.maximum.reduce([H - L, np.abs(H - pc), np.abs(L - pc)])
    atrp = pd.Series(tr).rolling(14, min_periods=5).mean().values / C
    return np.maximum(THETA_MIN, THETA_ATR * np.nan_to_num(atrp, nan=0.0))


def _zigzag(H, L, th, start):
    """start 봉의 저점에서 출발하는 인과적 지그재그. start부터 위로 간다고 보고 시작."""
    swings, d, ext, ei = [], "up", H[start], start
    for t in range(start + 1, len(H)):
        x = th[t - 1]
        if d == "up":
            if H[t] > ext:
                ext, ei = H[t], t
            elif L[t] <= ext * (1 - x):
                swings.append((ei, ext, "H"))
                d, ext, ei = "down", L[t], t
        else:
            if L[t] < ext:
                ext, ei = L[t], t
            elif H[t] >= ext * (1 + x):
                swings.append((ei, ext, "L"))
                d, ext, ei = "up", H[t], t
    return swings, d, ext


def vcp_checks(high, low, close, volume=None) -> dict:
    """마지막 봉 기준으로 VCP 점검표를 만든다. 돌파 전날까지의 데이터를 넣으면 돌파 직전 상태를 본다."""
    H, L, C = (np.asarray(x, dtype=float) for x in (high, low, close))
    n = len(C)
    if n < 60:
        return {"ok": False, "checks": [], "why": "데이터 부족"}
    lo = max(0, n - WINDOW)
    i1 = lo + int(np.argmax(H[lo:]))
    h1 = float(H[i1])
    if i1 >= n - 2:
        return {"ok": False, "checks": [], "why": "최근이 고점이라 베이스 없음"}
    il = i1 + int(np.argmin(L[i1:]))
    l1 = float(L[il])
    depths = [(h1 - l1) / h1]
    lows = [l1]

    th = _theta(H, L, C)
    swings, d, ext = _zigzag(H, L, th, il)
    pairs = [(swings[k][1], swings[k + 1][1]) for k in range(0, len(swings) - 1, 2)
             if swings[k][2] == "H" and swings[k + 1][2] == "L"]
    if d == "down" and swings and swings[-1][2] == "H":
        pairs.append((swings[-1][1], ext))          # 아직 끝나지 않은 마지막 조정
    for h, l in pairs:
        depths.append((h - l) / h)
        lows.append(l)
    pivot = pairs[-1][0] if pairs else float(H[il:].max())
    later_highs = [h for h, _ in pairs]

    close_now = float(C[-1])
    base_len = n - 1 - i1
    nT = len(depths)
    worst_shrink = max((depths[i + 1] / depths[i] for i in range(nT - 1)), default=0.0)
    last_ratio = depths[-1] / depths[0]
    min_low_ratio = min((lows[i + 1] / lows[i] for i in range(len(lows) - 1)), default=1.0)
    max_high_ratio = max((h / h1 for h in later_highs), default=0.0)
    rng10 = float((H[-10:].max() - L[-10:].min()) / H[-10:].max())

    checks = [
        Check("베이스 길이", base_len, f"{MIN_BASE_DAYS}일 이상", base_len >= MIN_BASE_DAYS),
        Check("수축 개수", nT, f"{MIN_T}~{MAX_T}번", MIN_T <= nT <= MAX_T),
        Check("첫 수축 깊이", round(depths[0], 3), f"{FIRST_MIN:.0%}~{FIRST_MAX:.0%}", FIRST_MIN <= depths[0] <= FIRST_MAX),
        Check("점점 얕아짐", round(worst_shrink, 2), f"다음/이전 {SHRINK_TOL} 이하", worst_shrink <= SHRINK_TOL),
        Check("마지막/첫 수축", round(last_ratio, 2), f"{LAST_VS_FIRST} 이하", last_ratio <= LAST_VS_FIRST),
        Check("마지막 수축 크기", round(depths[-1], 3), f"{LAST_MAX:.0%} 이하", depths[-1] <= LAST_MAX),
        Check("저점 상승", round(min_low_ratio, 3), f"다음/이전 {LOW_TOL} 이상", min_low_ratio >= LOW_TOL),
        Check("고점이 베이스 고점 이하", round(max_high_ratio, 3), f"{HIGH_TOL} 이하", max_high_ratio <= HIGH_TOL),
        Check("고점 근처", round(close_now / h1, 3), f"{NEAR_HIGH} 이상", close_now / h1 >= NEAR_HIGH),
        Check("피벗 대비 위치", round(close_now / pivot, 3), f"{EXT_MAX} 이하", close_now / pivot <= EXT_MAX),
        Check("직전 10일 변동폭(참고)", round(rng10, 3), "참고용", True, required=False),
    ]
    if volume is not None:
        v = np.asarray(volume, dtype=float)
        if len(v) >= 50 and np.nanmean(v[-50:]) > 0:
            ratio = float(np.nanmean(v[-10:]) / np.nanmean(v[-50:]))
            checks.append(Check("거래량 마름(참고)", round(ratio, 2), "0.85 이하면 마름", ratio <= 0.85, required=False))
    ok = all(c.passed for c in checks if c.required)
    return {"ok": ok, "checks": checks, "depths": [round(x * 100, 1) for x in depths], "pivot": pivot,
            "base_high": h1, "base_len": base_len, "failed": [c.name for c in checks if c.required and not c.passed]}
