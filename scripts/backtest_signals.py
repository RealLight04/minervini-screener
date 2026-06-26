"""
미너비니 신호 백테스트 — 스크리너의 '기술 신호'가 과거에 실제로 통했는가?

방법(시점복원, point-in-time):
  - 현재 미국 활성 유니버스의 장기 일봉을 새로 받아(메모리, DB 미오염)
  - 매월말 리밸런스 시점마다 그 시점까지의 데이터로만 신호를 재계산
  - 신호 버킷별 '미래 1·3·6개월 수익'을 유니버스 평균과 비교 → 엣지(초과수익) 측정

복제하는 신호(가격 기반, screener.py와 동일):
  트렌드 템플릿 8조건(close>MA150·MA200·MA50, MA150>MA200, MA200 상승,
  ≥52주저점×1.3, ≥52주고점×0.75, RS백분위≥70) → technical_pass.
  compute_signal의 AVOID(추세이탈)·SELL(50일선 하회)도 그대로.

한계(정직히 명시):
  1) 펀더멘털 게이트(final_pass)·피벗 돌파 타이밍은 시점복원이 어려워 제외 →
     '기술 신호'만 검증(스크린의 핵심 필터). STRONG_BUY 정밀 재현은 안 함.
  2) 생존편향: 현재 S&P500에 남아있는 종목만 → 탈락 종목 누락(수익 상방 편향).
     단, 버킷 간 '상대 비교'엔 편향이 비슷하게 작용해 엣지 판단엔 유효.

실행: python3 scripts/backtest_signals.py [--years N] [--sample N]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import yfinance as yf

from app.database import SessionLocal
from app.models import Stock
from config import settings

RS_THRESHOLD = 100 - settings.RS_TOP_PERCENTILE   # 70
FWD_WINDOWS = {"1M": 21, "3M": 63, "6M": 126}      # 거래일 기준 미래수익 창
LOOKBACK_RS = 252                                   # 52주
MIN_HISTORY = 252                                    # 신호 산출 최소 거래일
_OUT_CSV = str(Path(__file__).parent.parent / "backtest_results.csv")   # 리포 루트(gitignore)


def _universe(sample=None):
    db = SessionLocal()
    try:
        q = db.query(Stock.ticker).filter(Stock.market == "US", Stock.is_active == True)  # noqa: E712
        tickers = [t for (t,) in q.all()]
    finally:
        db.close()
    if sample:
        tickers = tickers[:sample]
    return tickers


def _download(tickers, years):
    end = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(years=years)
    print(f"  일봉 다운로드: {len(tickers)}종목 × {years}년 ({start.date()}~{end.date()})")
    df = yf.download(tickers, start=start.date().isoformat(), end=end.date().isoformat(),
                     auto_adjust=True, progress=False, threads=True)
    # 멀티/단일 종목 모두에서 'Close' 패널 추출
    if isinstance(df.columns, pd.MultiIndex):
        close = df["Close"].copy()
    else:
        close = df[["Close"]].copy()
        close.columns = tickers[:1]
    close = close.dropna(how="all")
    print(f"  종가 패널: {close.shape[0]}일 × {close.shape[1]}종목")
    return close


def _signal_bucket(c, ma50, ma150, ma200, ma200_prev, hi52, lo52, rs):
    """screener.py의 가격 기반 신호 복제. 반환: 'AVOID'/'SELL'/'TREND'/'NEUTRAL'."""
    # compute_signal: 추세 이탈 우선
    if c < ma200 or ma150 < ma200:
        return "AVOID"
    if c < ma50:
        return "SELL"
    # 추세 정상 → 트렌드 템플릿 8조건 충족 시 매수후보(TREND), 아니면 중립
    cond = [
        c > ma150, c > ma200, ma150 > ma200,
        (ma200_prev is not None and ma200 > ma200_prev),
        c > ma50, c >= lo52 * 1.30, c >= hi52 * 0.75,
        rs >= RS_THRESHOLD,
    ]
    return "TREND" if all(cond) else "NEUTRAL"


def run(years=6, sample=None):
    tickers = _universe(sample)
    close = _download(tickers, years)
    cols = list(close.columns)

    # 사전계산: 이동평균
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()
    ret52 = close / close.shift(LOOKBACK_RS) - 1.0     # 52주 수익률(RS 원천)
    hi52 = close.rolling(252).max()
    lo52 = close.rolling(252).min()

    # 월말 리밸런스 날짜(가용 데이터 안에서)
    month_ends = close.resample("ME").last().index
    rebal_dates = [d for d in month_ends if d in close.index]
    # 신호 산출엔 252일, 미래수익엔 최대 126일 필요 → 양끝 잘라냄
    idx = close.index
    usable = []
    for d in rebal_dates:
        pos = idx.get_loc(d)
        if pos >= MIN_HISTORY and pos + max(FWD_WINDOWS.values()) < len(idx):
            usable.append(d)
    print(f"  리밸런스 시점: {len(usable)}개월 ({usable[0].date()}~{usable[-1].date()})")

    col_pos = {tk: i for i, tk in enumerate(cols)}   # O(1) 열 위치 조회
    ma200_vals = ma200.values                          # ndarray 직접 접근(빠름)
    records = []
    for d in usable:
        pos = idx.get_loc(d)
        prev_pos = pos - 21
        # 그 시점 RS 백분위(횡단면): 유니버스 52주수익률 순위
        r52 = ret52.loc[d]
        valid_rs = r52.dropna()
        if len(valid_rs) < 10:
            continue
        rs_pct = valid_rs.rank(pct=True) * 100

        # 미래수익(각 창)
        fwd = {}
        for label, w in FWD_WINDOWS.items():
            fwd[label] = (close.iloc[pos + w] / close.loc[d] - 1.0) * 100

        # 유니버스 평균 미래수익(동일가중 벤치마크) — 신호 무관 전체
        for tk in cols:
            c = close.at[d, tk]
            if pd.isna(c) or c <= 0:
                continue
            m50, m150, m200 = ma50.at[d, tk], ma150.at[d, tk], ma200.at[d, tk]
            m200p = ma200_vals[prev_pos, col_pos[tk]] if prev_pos >= 0 else np.nan
            h, l = hi52.at[d, tk], lo52.at[d, tk]
            if any(pd.isna(x) for x in (m50, m150, m200, h, l)):
                continue
            if tk not in rs_pct.index:
                continue
            bucket = _signal_bucket(c, m50, m150, m200,
                                    None if pd.isna(m200p) else m200p,
                                    h, l, rs_pct[tk])
            rec = {"date": d, "ticker": tk, "bucket": bucket}
            for label in FWD_WINDOWS:
                fv = (close.iloc[pos + FWD_WINDOWS[label]][tk] / c - 1.0) * 100
                rec[label] = fv
            records.append(rec)

    res = pd.DataFrame.from_records(records)
    if res.empty:
        print("결과 없음")
        return
    res.to_csv(_OUT_CSV, index=False)
    print(f"  원자료 저장: {_OUT_CSV}")
    _report(res)


def _report(res):
    print("\n" + "=" * 64)
    print("백테스트 결과 — 신호 버킷별 미래수익 (%, 동일가중 평균)")
    print("=" * 64)
    n_obs = len(res)
    n_months = res["date"].nunique()
    print(f"관측치 {n_obs:,}건 · {n_months}개월 · 종목 {res['ticker'].nunique()}개\n")

    # 전체(벤치마크) 평균
    bench = {lab: res[lab].mean() for lab in FWD_WINDOWS}
    order = ["TREND", "NEUTRAL", "SELL", "AVOID"]
    labels = {"TREND": "트렌드통과(매수후보)", "NEUTRAL": "중립", "SELL": "SELL(50일선↓)", "AVOID": "AVOID(추세이탈)"}

    header = f"{'버킷':<22}{'건수':>8}"
    for lab in FWD_WINDOWS:
        header += f"{lab+'평균':>9}{lab+'승률':>8}"
    print(header)
    print("-" * len(header))
    for b in order:
        sub = res[res["bucket"] == b]
        if sub.empty:
            continue
        line = f"{labels[b]:<22}{len(sub):>8,}"
        for lab in FWD_WINDOWS:
            line += f"{sub[lab].mean():>8.1f}%{(sub[lab] > 0).mean()*100:>7.0f}%"
        print(line)
    # 벤치마크
    line = f"{'─ 유니버스 전체(벤치)':<22}{n_obs:>8,}"
    for lab in FWD_WINDOWS:
        line += f"{bench[lab]:>8.1f}%{(res[lab] > 0).mean()*100:>7.0f}%"
    print(line)

    print("\n[엣지: 트렌드통과 − 유니버스 평균]")
    sub = res[res["bucket"] == "TREND"]
    for lab in FWD_WINDOWS:
        edge = sub[lab].mean() - bench[lab]
        print(f"  {lab}: {edge:+.2f}%p (트렌드 {sub[lab].mean():.1f}% vs 벤치 {bench[lab]:.1f}%)")

    print("\n[연도별 트렌드통과 vs 벤치 — 3M 평균]")
    res["year"] = res["date"].dt.year
    for y, g in res.groupby("year"):
        t = g[g["bucket"] == "TREND"]["3M"]
        b = g["3M"]
        if len(t) == 0:
            continue
        print(f"  {y}: 트렌드 {t.mean():+6.1f}%  벤치 {b.mean():+6.1f}%  엣지 {t.mean()-b.mean():+5.1f}%p  (n={len(t)})")

    print("\n⚠️ 한계: 펀더멘털·피벗타이밍 제외(기술신호만) · 생존편향(현 S&P500 잔존종목)")


if __name__ == "__main__":
    yrs, smp = 6, None
    a = sys.argv[1:]
    i = 0
    while i < len(a):
        if a[i] == "--years":
            yrs = int(a[i + 1]); i += 2
        elif a[i] == "--sample":
            smp = int(a[i + 1]); i += 2
        else:
            i += 1
    run(years=yrs, sample=smp)
