"""
한국(코스피/코스닥) 종목 분기 재무를 OpenDART에서 수집 → Fundamental(Q) 갱신.
yfinance보다 분기 이력이 길어 '진짜 YoY 3분기 가속' 판정이 가능해진다.
수집 후 recompute_yoy.py로 YoY를 채울 것.
실행: python3 scripts/collect_dart.py [TICKER ...]   # 인자 주면 해당 종목만
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_dart")

from app.database import SessionLocal
from app.data_fetcher import fetch_dart_quarterly
from app.models import Stock
from config import settings

YEARS = [2024, 2025, 2026]


def main(only=None):
    key = settings.DART_API_KEY
    if not key:
        log.error("DART_API_KEY 없음 — .env 확인")
        return
    import opendartreader
    dart = opendartreader.OpenDartReader(key)

    db = SessionLocal()
    try:
        q = db.query(Stock).filter(Stock.market.in_(["KOSPI", "KOSDAQ"]), Stock.is_active == True)
        if only:
            q = q.filter(Stock.ticker.in_(only))
        stocks = q.all()
        log.info(f"DART 한국 재무 수집: {len(stocks)}종목")
        ok = 0
        for i, s in enumerate(stocks, 1):
            try:
                if fetch_dart_quarterly(db, dart, s.ticker, YEARS):
                    ok += 1
            except Exception as e:
                log.warning(f"{s.ticker} 실패: {e}")
            if i % 25 == 0:
                log.info(f"  진행 {i}/{len(stocks)} (성공 {ok})")
            time.sleep(0.1)
        log.info(f"DART 수집 완료: {ok}/{len(stocks)}")
    finally:
        db.close()


if __name__ == "__main__":
    main(only=sys.argv[1:] or None)
