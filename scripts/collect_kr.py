"""
한국(코스피/코스닥) 종목 수집 + 전체 재스크린.
시가총액 상위(config의 KOSPI_TOP_N / KOSDAQ_TOP_N)만 수집한다.
실행: python3 scripts/collect_kr.py
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_kr")

from app.database import SessionLocal
from app.data_fetcher import (
    ensure_stocks_in_db,
    fetch_and_save_fundamentals,
    fetch_and_save_prices,
    fetch_company_info,
    fetch_kr_tickers,
)
from app.screener import run_daily_screen
from config import settings


def main():
    db = SessionLocal()
    try:
        tickers = fetch_kr_tickers(settings.KOSPI_TOP_N, settings.KOSDAQ_TOP_N)
        if not tickers:
            log.error("한국 종목 목록을 가져오지 못했습니다.")
            return
        log.info(f"한국 종목 {len(tickers)}개 (코스피 상위 {settings.KOSPI_TOP_N} + 코스닥 상위 {settings.KOSDAQ_TOP_N})")
        ensure_stocks_in_db(db, tickers)

        ok = 0
        for i, item in enumerate(tickers, 1):
            tk = item["ticker"]
            try:
                if fetch_and_save_prices(db, tk):
                    ok += 1
                fetch_and_save_fundamentals(db, tk)
                fetch_company_info(db, tk)  # ROE·마진·목표가·다음 실적일
            except Exception as e:
                log.warning(f"{tk} 수집 실패: {e}")
            if i % 25 == 0:
                log.info(f"  진행 {i}/{len(tickers)} (가격 성공 {ok})")
            time.sleep(0.2)  # yfinance 과부하 방지
        log.info(f"수집 완료: 가격 {ok}/{len(tickers)}")

        passed = run_daily_screen(db)
        log.info(f"전체 재스크린 완료: final_pass {passed}종목")
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
