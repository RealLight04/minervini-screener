# -*- coding: utf-8 -*-
"""VCP 점수 모델. 순수 함수 모듈이라 DB, 네트워크, 기존 screener.py에 의존하지 않는다.

설계 근거는 프로젝트 규칙 문서(글쓰기.txt)다.
  - VCP는 참/거짓이 아니라 100점 점수로 본다. 항목마다 점수를 따로 계산하고 따로 시험할 수 있게 나눴다.
  - 고정 숫자(20, 10, 5%나 T2 = T1의 절반)로 정의하지 않는다. 수축폭이 줄어드는 정도를 연속 점수로 매긴다.
  - 추세(Trend Template)와 VCP 구조를 코드에서 분리한다. 추세는 점수 항목 하나일 뿐 VCP의 정의가 아니다.
  - 거래량은 합격 조건이 아니라 점수 항목이다. 돌파는 가격이 피벗을 넘으면 감지하고 거래량은 등급만 매긴다.
  - 이 도구는 VCP를 확정하지 않는다. 사람이 차트를 볼 가치가 있는 후보를 품질 순으로 올리는 용도다.

수축 정의(내가 고른 방식, 규칙 문서에 있는 건 아니다):
  T1은 베이스 고점(최근 150거래일 최고가)에서 가장 깊은 저점까지 한 번으로 센다. 그 사이의 반등은 T1 안에 묶는다
  (책 그림 10.5 GRA 사례에서 미너비니가 T1을 한 번으로 센 방식). 가장 깊은 저점 이후로는 ATR 적응형 지그재그로
  T2, T3...를 센다. 아직 끝나지 않은 마지막 조정도 센다. 피벗은 마지막 수축의 상단이다.

임계값(config.py의 VCP_*)과 점수 구간표(아래 score_* 함수)는 초기 휴리스틱이다. 과거 성과에 맞춰 조정하지 말고
사람 라벨과 레지스트리에 쌓이는 실제 결과로 표본외 검증한 뒤에만 바꾼다.

입력 배열은 모두 과거에서 현재 순서(오래된 것이 앞)다. 인덱스는 입력 배열 기준 절대 위치이고, 날짜 매핑은 호출하는 쪽이 한다.
"""
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np

from config import settings

# 임계값은 config.py의 VCP_* 에서 읽는다(프로젝트 규칙: 매직넘버는 config.py). 뜻은 그쪽 주석 참고.
THETA_MIN, THETA_ATR = settings.VCP_THETA_MIN, settings.VCP_THETA_ATR   # 지그재그 반전 임계 = max(THETA_MIN, THETA_ATR x ATR14%)
WINDOW = settings.VCP_WINDOW
MIN_BASE_DAYS = settings.VCP_MIN_BASE_DAYS
MIN_DEPTH1 = settings.VCP_MIN_DEPTH1
MAX_DEPTH1 = settings.VCP_MAX_DEPTH1
NEAR_PIVOT_PCT = settings.VCP_NEAR_PIVOT_PCT
MIN_SCORE = settings.VCP_MIN_SCORE
MAX_BACK = settings.VCP_MAX_BACK
VOL_AVG_DAYS = 50       # 돌파 거래량 비교용 평균 거래일
UPTREND_BARS, UPTREND_CHUNKS = 126, 3   # 상승 구조: 최근 약 6개월을 3등분
RECENT_HIGH = "최근이 고점이라 베이스 없음"

# 규칙 문서 점수표 그대로. 문서는 합계를 100점이라 적었지만 항목을 더하면 95점이라, 총점은 100점으로 환산한다.
# (빠진 5점을 어느 항목에 줄지 정해지면 여기만 고치면 된다)
WEIGHTS = {"trend": 20, "uptrend": 10, "contraction": 20, "volume": 15,
           "higher_low": 5, "tightness": 10, "proximity": 5, "rs": 10}
MAX_RAW = sum(WEIGHTS.values())

WATCH, NEAR_PIVOT, BREAKOUT, FAILED_BREAKOUT, INVALIDATED = (
    "WATCH", "NEAR_PIVOT", "BREAKOUT", "FAILED_BREAKOUT", "INVALIDATED")

# 돌파 거래량 비율 등급 경계: <1.0 약함, 1.0~1.2 보통, 1.2~1.5 좋음, 1.5~2.0 매우 좋음, 2.0~ 매우 강함
QUALITY_EDGES = (1.0, 1.2, 1.5, 2.0)
QUALITY_LABELS = ("약함", "보통", "좋음", "매우 좋음", "매우 강함")

# 수축 개수 점수(6점 만점): 1개뿐이면 낮게, 3~5개가 높은 품질, 너무 많으면 조금 감점
_COUNT_PTS = {1: 1.0, 2: 3.5, 3: 5.0, 4: 6.0, 5: 6.0, 6: 5.0}


@dataclass
class Contraction:
    peak_idx: int           # 수축 시작(고점) 위치
    low_idx: int
    peak_price: float
    low_price: float
    depth_pct: float        # (고점 - 저점) / 고점 x 100
    duration: int           # 고점에서 저점까지 거래일
    avg_volume: float | None

    @property
    def depth(self) -> float:
        return self.depth_pct / 100


@dataclass
class Formation:
    """한 번 탐지된 VCP 형성. base_high_idx가 형성의 신원(같은 베이스면 같은 값)이다."""
    base_high_idx: int
    base_high: float
    base_low_idx: int
    base_low: float
    pivot: float
    pivot_idx: int
    last_low_idx: int
    detected_idx: int
    contractions: list[Contraction] = field(default_factory=list)

    @property
    def key(self) -> int:
        return self.base_high_idx


@dataclass
class Component:
    name: str
    points: float
    max_points: float
    detail: dict = field(default_factory=dict)


@dataclass
class VCPScore:
    total: float            # 100점 환산. 등급과 MIN_SCORE는 이 값 기준
    grade: str | None
    components: dict[str, Component]
    raw: float = 0.0        # 항목 점수 단순 합(MAX_RAW 만점)

    def points(self) -> dict[str, float]:
        return {k: round(c.points, 1) for k, c in self.components.items()}


@dataclass
class BreakoutRecord:
    idx: int
    price: float
    pivot: float
    volume: float | None
    average_volume: float | None
    volume_ratio: float | None
    quality: int | None             # 0~4, QUALITY_LABELS 인덱스. 거래량이 없으면 None
    quality_label: str | None


@dataclass
class Analysis:
    formation: Formation | None
    score: VCPScore | None
    state: str | None
    breakout: BreakoutRecord | None
    eligible: bool
    reason: str | None = None
    distance_to_pivot: float | None = None      # (피벗 - 종가) / 피벗. 음수면 피벗 위로 연장된 정도
    breakout_age: int | None = None             # 돌파 봉 이후 지난 거래일(돌파 당일 0)


def _ramp(x, xs, ys) -> float:
    """구간별 선형 점수(0~1). xs는 오름차순. 범위 밖은 양 끝 값으로 고정."""
    if x is None or not np.isfinite(x):
        return 0.0
    return float(np.interp(x, xs, ys))


def _blend(pairs) -> float:
    """평균과 최악을 반반. 한 번만 크게 어긋나도 점수에 반영된다."""
    return 0.5 * float(np.mean(pairs)) + 0.5 * float(np.min(pairs))


def grade_of(total: float) -> str | None:
    for edge, g in ((90, "A+"), (80, "A"), (70, "B"), (60, "C")):
        if total >= edge:
            return g
    return None


# ---------------------------------------------------------------- 수축 탐지

def _theta(H, L, C):
    pc = np.r_[C[0], C[:-1]]
    tr = np.maximum.reduce([H - L, np.abs(H - pc), np.abs(L - pc)])
    cs = np.cumsum(np.r_[0.0, tr])
    idx = np.arange(len(tr))
    lo = np.maximum(0, idx - 13)
    cnt = idx - lo + 1
    atrp = np.where(cnt >= 5, (cs[idx + 1] - cs[lo]) / cnt / C, 0.0)
    return np.maximum(THETA_MIN, THETA_ATR * atrp)


def _zigzag(H, L, th, start):
    """start 봉의 저점에서 출발하는 인과적 지그재그. (idx, price, 'H'|'L') 목록과 마지막 방향, 진행 중인 극값."""
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
    return swings, d, ext, ei


def _avg_vol(V, a, b):
    if V is None:
        return None
    seg = V[a:b + 1]
    seg = seg[np.isfinite(seg)]
    return float(seg.mean()) if len(seg) else None


def _arr(x):
    return None if x is None else np.asarray(x, dtype=float)


def _detect(high, low, close, volume=None, asof=None):
    H, L, C, V = _arr(high), _arr(low), _arr(close), _arr(volume)
    if asof is not None:
        n = asof + 1
        H, L, C = H[:n], L[:n], C[:n]
        V = None if V is None else V[:n]
    n = len(C)
    if n < 60:
        return None, "데이터 부족"
    lo = max(0, n - WINDOW)
    i1 = lo + int(np.argmax(H[lo:]))
    if n - 1 - i1 < MIN_BASE_DAYS:
        return None, RECENT_HIGH
    h1 = float(H[i1])
    il = i1 + int(np.argmin(L[i1:]))
    l1 = float(L[il])
    depth1 = (h1 - l1) / h1
    if depth1 < MIN_DEPTH1:
        return None, "조정폭이 너무 작음"
    if depth1 > MAX_DEPTH1:
        return None, "베이스가 너무 깊음"

    def make(pi, pp, li, lp):
        return Contraction(pi, li, float(pp), float(lp), round(float((pp - lp) / pp * 100), 2), li - pi, _avg_vol(V, pi, li))

    cons = [make(i1, h1, il, l1)]
    swings, d, ext, ei = _zigzag(H, L, _theta(H, L, C), il)
    for k in range(0, len(swings) - 1, 2):
        (pi, pp, _), (li, lp, _) = swings[k], swings[k + 1]
        cons.append(make(pi, pp, li, lp))
    if d == "down" and swings:
        pi, pp, _ = swings[-1]
        cons.append(make(pi, pp, ei, ext))          # 아직 끝나지 않은 마지막 조정
    last = cons[-1]
    f = Formation(base_high_idx=i1, base_high=h1, base_low_idx=il, base_low=l1, pivot=last.peak_price,
                  pivot_idx=last.peak_idx, last_low_idx=last.low_idx, detected_idx=n - 1, contractions=cons)
    return f, None


def detect(high, low, close, volume=None, asof=None) -> Formation | None:
    """asof 위치까지의 데이터로 형성을 찾는다. 없으면 None. asof가 없으면 마지막 봉 기준."""
    return _detect(high, low, close, volume, asof)[0]


def detect_latest(high, low, close, volume=None, max_back=MAX_BACK) -> tuple[Formation | None, str | None]:
    """마지막 봉 기준 형성. 돌파로 최근 봉이 새 고점이 되면 베이스 고점이 오늘로 옮겨져 형성이 사라지므로,
    그때는 최대 max_back봉 거슬러 올라가 돌파 직전의 형성을 찾는다(상태는 evaluate_state가 현재 종가로 다시 본다)."""
    f, reason = _detect(high, low, close, volume)
    if f is not None or reason != RECENT_HIGH:
        return f, reason
    n = len(close)
    for back in range(1, max_back + 1):
        f, _ = _detect(high, low, close, volume, n - 1 - back)
        if f is not None:
            return f, None
    return None, reason


# ---------------------------------------------------------------- 항목별 점수

def trend_ratio(high, low, close) -> tuple[float, dict]:
    """Trend Template 8조건(RS 제외) 중 통과 비율. 계산할 데이터가 모자란 조건은 실패로 센다."""
    H, L, C = _arr(high), _arr(low), _arr(close)
    n = len(C)
    c = float(C[-1])
    ma = {k: float(C[-k:].mean()) if n >= k else None for k in (50, 150, 200)}
    ma200_prev = float(C[-222:-22].mean()) if n >= 222 else None
    lo52, hi52 = float(L[-252:].min()), float(H[-252:].max())

    def gt(a, b):
        return a is not None and b is not None and a > b

    conds = {
        "종가>50일선": gt(c, ma[50]), "종가>150일선": gt(c, ma[150]), "종가>200일선": gt(c, ma[200]),
        "50일선>150일선": gt(ma[50], ma[150]), "150일선>200일선": gt(ma[150], ma[200]),
        "200일선 상승": gt(ma[200], ma200_prev),
        "52주 저점 +30%": c >= lo52 * 1.30, "52주 고점 -25% 이내": c >= hi52 * 0.75,
    }
    return sum(conds.values()) / len(conds), conds


def score_trend(ratio: float, detail: dict | None = None) -> Component:
    ratio = min(max(ratio, 0.0), 1.0)
    return Component("trend", WEIGHTS["trend"] * ratio, WEIGHTS["trend"], dict(detail or {}, ratio=round(ratio, 2)))


def score_uptrend(high, low) -> Component:
    """최근 6개월을 3등분해서 고점과 저점이 2% 허용오차 안에서 계속 올라왔는지(4번 비교)."""
    w = WEIGHTS["uptrend"]
    H, L = _arr(high), _arr(low)
    if len(H) < UPTREND_BARS:
        return Component("uptrend", 0.0, w, {"why": "데이터 부족"})
    H, L = H[-UPTREND_BARS:], L[-UPTREND_BARS:]
    size = UPTREND_BARS // UPTREND_CHUNKS
    hi = [float(H[i * size:(i + 1) * size].max()) for i in range(UPTREND_CHUNKS)]
    lw = [float(L[i * size:(i + 1) * size].min()) for i in range(UPTREND_CHUNKS)]
    hh = [hi[i + 1] >= hi[i] * 0.98 for i in range(UPTREND_CHUNKS - 1)]
    hl = [lw[i + 1] >= lw[i] * 0.98 for i in range(UPTREND_CHUNKS - 1)]
    ok = sum(hh) + sum(hl)
    return Component("uptrend", w * ok / (len(hh) + len(hl)), w, {"higher_highs": hh, "higher_lows": hl})


def score_contraction(depths) -> Component:
    """depths는 T1, T2, T3...의 낙폭(소수). 개수 6점 + 단계별 감소 8점 + 마지막/첫 비율 6점."""
    w = WEIGHTS["contraction"]
    n = len(depths)
    count = _COUNT_PTS.get(n, 3.0 if n > 6 else 0.0)
    step = overall = 0.0
    detail = {"count": n, "depths_pct": [round(d * 100, 1) for d in depths]}
    if n >= 2:
        ratios = [depths[i + 1] / depths[i] for i in range(n - 1)]
        pairs = [_ramp(r, (0.5, 0.8, 1.0, 1.3), (1.0, 0.75, 0.35, 0.0)) for r in ratios]
        step = 8 * _blend(pairs)
        last_vs_first = depths[-1] / depths[0]
        overall = 6 * _ramp(last_vs_first, (0.25, 0.4, 0.6, 1.0), (1.0, 1.0, 0.6, 0.0))
        detail.update(ratios=[round(r, 2) for r in ratios], last_vs_first=round(last_vs_first, 2))
    detail.update(count_pts=round(count, 1), step_pts=round(step, 1), overall_pts=round(overall, 1))
    return Component("contraction", count + step + overall, w, detail)


def score_volume(avg_volumes, dry_ratio) -> Component:
    """수축 구간별 평균 거래량이 줄어드는 정도 9점 + 최근 10일 거래량 / 50일 평균(dry-up) 6점."""
    w = WEIGHTS["volume"]
    vols = [v for v in avg_volumes if v is not None and v > 0]
    detail = {"avg_volumes": [None if v is None else round(v) for v in avg_volumes],
              "dry_ratio": None if dry_ratio is None else round(dry_ratio, 2)}
    shrink = 0.0
    if len(vols) >= 2 and len(vols) == len(avg_volumes):
        pairs = [_ramp(vols[i + 1] / vols[i], (0.6, 0.85, 1.0, 1.3), (1.0, 0.8, 0.4, 0.0)) for i in range(len(vols) - 1)]
        shrink = 9 * _blend(pairs)
    dry = 6 * _ramp(dry_ratio, (0.5, 0.7, 0.85, 1.0, 1.2), (1.0, 0.9, 0.6, 0.25, 0.0))
    detail.update(shrink_pts=round(shrink, 1), dry_pts=round(dry, 1))
    return Component("volume", shrink + dry, w, detail)


def score_higher_low(lows) -> Component:
    """수축 저점이 올라가는 정도. 저점 하나가 없다고 제외하지 않고 점수만 깎는다."""
    w = WEIGHTS["higher_low"]
    if len(lows) < 2:
        return Component("higher_low", 0.0, w, {"lows": [round(x, 2) for x in lows]})
    ratios = [lows[i + 1] / lows[i] for i in range(len(lows) - 1)]
    pairs = [_ramp(r, (0.92, 0.97, 1.0), (0.0, 0.5, 1.0)) for r in ratios]
    return Component("higher_low", w * _blend(pairs), w, {"ratios": [round(r, 3) for r in ratios]})


def score_tightness(range5, range10) -> Component:
    """최근 5일과 10일 고저 범위(고점 대비). 3%대는 매우 강함, 5%대 강함, 8%대 보통, 그 이상 약함."""
    w = WEIGHTS["tightness"]

    def s(r):
        return _ramp(r, (0.03, 0.05, 0.08, 0.12), (1.0, 0.75, 0.4, 0.0))
    return Component("tightness", w * (0.5 * s(range5) + 0.5 * s(range10)), w,
                     {"range5_pct": round(range5 * 100, 1), "range10_pct": round(range10 * 100, 1)})


def score_proximity(dist) -> Component:
    """피벗까지 거리 (피벗 - 종가) / 피벗. 음수면 피벗 위. 2% 이내 만점, 8%를 넘으면 낮음. 위로 5% 넘게 연장되면 감점."""
    w = WEIGHTS["proximity"]
    if dist >= 0:
        v = _ramp(dist, (0.02, 0.05, 0.08, 0.12, 0.20), (1.0, 0.7, 0.4, 0.1, 0.0))
    else:
        v = _ramp(-dist, (0.02, 0.05, 0.10), (1.0, 0.6, 0.0))
    return Component("proximity", w * v, w, {"distance_pct": round(dist * 100, 1)})


def score_rs(rs) -> Component:
    w = WEIGHTS["rs"]
    if rs is None:
        return Component("rs", 0.0, w, {"rs": None})
    return Component("rs", w * _ramp(rs, (30, 50, 70, 80, 90, 99), (0.0, 0.25, 0.55, 0.75, 0.9, 1.0)), w, {"rs": rs})


def score_formation(f: Formation, high, low, close, volume=None, rs=None, trend=None, asof=None) -> VCPScore:
    """형성의 VCP 점수. asof 기본값은 탐지 시점이다(돌파 뒤에 다시 매겨도 돌파 전 모습을 기준으로 하려고).

    trend는 호출 쪽이 이미 가진 Trend Template 통과 비율(0~1)이다. 없으면 가격 배열로 8조건을 직접 센다.
    """
    asof = f.detected_idx if asof is None else asof
    n = asof + 1
    H, L, C = _arr(high)[:n], _arr(low)[:n], _arr(close)[:n]
    V = None if volume is None else _arr(volume)[:n]
    if trend is None:
        ratio, tdetail = trend_ratio(H, L, C)
    else:
        ratio, tdetail = float(trend), {"source": "provided"}
    cons = f.contractions
    dry = None
    if V is not None and len(V) >= 50 and np.nanmean(V[-50:]) > 0:
        dry = float(np.nanmean(V[-10:]) / np.nanmean(V[-50:]))
    r5 = float((H[-5:].max() - L[-5:].min()) / H[-5:].max())
    r10 = float((H[-10:].max() - L[-10:].min()) / H[-10:].max())
    comps = [
        score_trend(ratio, tdetail),
        score_uptrend(H, L),
        score_contraction([c.depth for c in cons]),
        score_volume([c.avg_volume for c in cons], dry),
        score_higher_low([c.low_price for c in cons]),
        score_tightness(r5, r10),
        score_proximity((f.pivot - float(C[-1])) / f.pivot),
        score_rs(rs),
    ]
    raw = round(sum(c.points for c in comps), 1)
    total = round(raw * 100 / MAX_RAW, 1)
    return VCPScore(total, grade_of(total), {c.name: c for c in comps}, raw)


# ---------------------------------------------------------------- 상태와 돌파

def _breakout_record(f: Formation, b: int, close, volume) -> BreakoutRecord:
    C = _arr(close)
    vol = avg = ratio = quality = label = None
    if volume is not None:
        V = _arr(volume)
        vol = float(V[b])
        prev = V[max(0, b - VOL_AVG_DAYS):b]
        prev = prev[np.isfinite(prev)]
        if len(prev) >= 20 and prev.mean() > 0 and np.isfinite(vol):
            avg = float(prev.mean())
            ratio = vol / avg
            quality = bisect_right(QUALITY_EDGES, ratio)
            label = QUALITY_LABELS[quality]
    return BreakoutRecord(b, float(C[b]), f.pivot, vol, avg, ratio, quality, label)


def evaluate_state(f: Formation, close, volume=None) -> tuple[str, BreakoutRecord | None]:
    """고정된 형성(피벗, 베이스 저점)이 지금 어느 상태인지. 형성은 탐지 시점의 것을 그대로 쓴다.

    INVALIDATED  탐지 뒤에 베이스 저점 아래로 종가 마감(베이스가 무너짐). 새로 탐지한 형성은 저점이 곧 베이스 저점이라
                 이 상태가 될 수 없고, 저장해 둔 형성을 이어서 평가할 때만 나온다
    BREAKOUT     종가가 피벗 위. 거래량은 돌파 기록의 등급으로만 쓴다. 오래 전에 돌파해 많이 연장됐는지는
                 Analysis.breakout_age와 distance_to_pivot으로 호출하는 쪽이 거른다
    FAILED_BREAKOUT  한 번 피벗 위에서 마감했다가 지금 종가가 피벗 아래
    NEAR_PIVOT / WATCH  아직 돌파 전이고 피벗까지 거리가 NEAR_PIVOT_PCT 이내인지로 나눈다
    """
    C = _arr(close)
    if (C[f.base_low_idx + 1:] < f.base_low).any():
        return INVALIDATED, None
    above = np.nonzero(C[f.pivot_idx + 1:] > f.pivot)[0]
    if len(above):
        rec = _breakout_record(f, f.pivot_idx + 1 + int(above[0]), C, volume)
        return (FAILED_BREAKOUT if C[-1] < f.pivot else BREAKOUT), rec
    dist = (f.pivot - C[-1]) / f.pivot
    return (NEAR_PIVOT if dist <= NEAR_PIVOT_PCT else WATCH), None


def analyze(high, low, close, volume=None, rs=None, trend=None, formation: Formation | None = None) -> Analysis:
    """마지막 봉 기준 전체 분석. formation을 주면 새로 찾지 않고 그 형성(레지스트리에 저장된 것)을 이어서 평가한다."""
    reason = None
    f = formation
    if f is None:
        f, reason = detect_latest(high, low, close, volume)
    if f is None:
        return Analysis(None, None, None, None, False, reason)
    state, rec = evaluate_state(f, close, volume)
    C = _arr(close)
    score_at = rec.idx - 1 if rec is not None else len(C) - 1   # 돌파가 있었으면 돌파 전날 모습으로 점수를 매긴다
    score = score_formation(f, high, low, close, volume, rs, trend, asof=score_at)
    return Analysis(f, score, state, rec, score.total >= MIN_SCORE and state != INVALIDATED, None,
                    (f.pivot - float(C[-1])) / f.pivot, None if rec is None else len(C) - 1 - rec.idx)
