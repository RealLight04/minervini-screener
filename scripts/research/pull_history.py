# -*- coding: utf-8 -*-
"""연구/백테스트용 장기 가격 히스토리 수집기 (프로덕션 screener.db와 분리).

프로덕션 data_fetcher는 450일치만 보관하므로(정리 주기와 맞춤), 장기 백테스트에는
부족하다. 이 스크립트는 screener.db의 활성 종목 목록만 읽어, 별도 SQLite
(scripts/research/data/prices.sqlite, gitignore)로 N년치를 받는다.

사용:
    python scripts/research/pull_history.py                 # 기본 2021-01-01부터
    python scripts/research/pull_history.py 2019-01-01       # 시작일 지정

US: yfinance 벌크. KOSPI/KOSDAQ: FinanceDataReader(.KS/.KQ 접미사 제거).
finance-datareader가 없으면 KR은 건너뛴다(pip install finance-datareader).
"""
import os
import sqlite3
import sys
import time

import pandas as pd
import yfinance as yf

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "screener.db")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(DATA_DIR, "prices.sqlite")
START = sys.argv[1] if len(sys.argv) > 1 else "2021-01-01"


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(SRC)
    stocks = pd.read_sql("SELECT id, ticker, market FROM stocks WHERE is_active=1", con)
    con.close()

    out = sqlite3.connect(OUT)
    out.execute("DROP TABLE IF EXISTS prices")
    out.execute("CREATE TABLE prices (ticker TEXT, market TEXT, date TEXT, close REAL, volume REAL)")
    out.execute("CREATE INDEX idx_tkr ON prices(ticker)")
    rows = 0

    us = stocks[stocks.market == "US"]["ticker"].tolist()
    t0 = time.time()
    for i in range(0, len(us), 100):
        batch = us[i:i + 100]
        df = yf.download(batch, start=START, auto_adjust=True, group_by="ticker",
                         threads=True, progress=False)
        for tk in batch:
            try:
                sub = df[tk][["Close", "Volume"]].dropna()
            except Exception:
                continue
            if sub.empty:
                continue
            recs = [(tk, "US", d.strftime("%Y-%m-%d"), float(c), float(v))
                    for d, c, v in zip(sub.index, sub["Close"], sub["Volume"])]
            out.executemany("INSERT INTO prices VALUES (?,?,?,?,?)", recs)
            rows += len(recs)
        out.commit()
        print(f"US {i}~{i + len(batch)}: 누적 {rows}행 ({time.time() - t0:.0f}s)", flush=True)

    kr = stocks[stocks.market.isin(["KOSPI", "KOSDAQ"])]
    try:
        import FinanceDataReader as fdr
    except ImportError:
        print("finance-datareader 미설치 — KR 건너뜀 (pip install finance-datareader)", flush=True)
        kr = kr.iloc[0:0]
    ok = fail = 0
    for j, (_, s) in enumerate(kr.iterrows()):
        try:
            h = fdr.DataReader(s.ticker.split(".")[0], START)
            if h.empty:
                fail += 1
                continue
            recs = [(s.ticker, s.market, d.strftime("%Y-%m-%d"), float(c), float(v))
                    for d, c, v in zip(h.index, h["Close"], h["Volume"])]
            out.executemany("INSERT INTO prices VALUES (?,?,?,?,?)", recs)
            rows += len(recs)
            ok += 1
        except Exception:
            fail += 1
        if (j + 1) % 50 == 0:
            out.commit()
            print(f"KR {j + 1}/{len(kr)}: ok={ok} fail={fail} 누적 {rows}행", flush=True)
    out.commit()

    cur = out.execute("SELECT COUNT(DISTINCT ticker), MIN(date), MAX(date), COUNT(*) FROM prices")
    n_tk, mn, mx, n = cur.fetchone()
    out.close()
    print(f"\n완료: {n_tk}종목 {mn}~{mx} 총 {n}행 → {OUT}")


if __name__ == "__main__":
    main()
