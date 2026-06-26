"""
일일 데이터 수집 + 스크리닝 실행 스크립트
APScheduler로 장 마감 후 자동 실행, 또는 python scripts/daily_update.py 로 수동 실행
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal, init_db
from app.data_fetcher import (
    ensure_stocks_in_db,
    fetch_and_save_fundamentals,
    fetch_and_save_prices,
    fetch_company_info,
    fetch_sp500_tickers,
)
from app.screener import run_daily_screen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run():
    init_db()
    db = SessionLocal()
    try:
        # 1. S&P 500 종목 목록 동기화
        logger.info("S&P 500 종목 목록 수집 중...")
        stock_list = fetch_sp500_tickers()
        ensure_stocks_in_db(db, stock_list)
        tickers = [s["ticker"] for s in stock_list]
        logger.info(f"{len(tickers)}개 종목 확인 완료")

        # 2. 주가 데이터 수집
        logger.info("주가 데이터 수집 중...")
        ok_count = 0
        for i, ticker in enumerate(tickers, 1):
            if fetch_and_save_prices(db, ticker):
                ok_count += 1
            if i % 50 == 0:
                logger.info(f"  {i}/{len(tickers)} 처리 중...")
        logger.info(f"주가 수집 완료: {ok_count}/{len(tickers)}")

        # 3. 재무 데이터 수집 (주 1회면 충분하지만 매일 실행해도 무방)
        logger.info("재무 데이터 수집 중...")
        for ticker in tickers:
            fetch_and_save_fundamentals(db, ticker)

        # 3-1. 기업 기본정보(ROE·마진·목표가·다음 실적일) 갱신
        #   next_earnings는 .calendar의 '미래 예정일'이라 매일 최신화해야 의미가 있다.
        logger.info("기업 기본정보 갱신 중...")
        for ticker in tickers:
            fetch_company_info(db, ticker)

        # 4. 스크리닝 실행
        logger.info("Minervini 스크리닝 실행 중...")
        passed = run_daily_screen(db)
        logger.info(f"최종 통과 종목: {passed}개")

    finally:
        db.close()


if __name__ == "__main__":
    run()
