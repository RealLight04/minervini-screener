"""
기업 기본정보(ROE·마진·목표주가·투자의견·다음 실적일) 수집.
yfinance .info를 종목별로 호출 → stocks 테이블에 저장.
실행: python3 scripts/collect_fundamentals.py
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_fund")

from app.database import SessionLocal, engine
from app.data_fetcher import fetch_company_info
from app.models import Stock

NEW_COLS = {
    "roe": "FLOAT", "profit_margin": "FLOAT", "operating_margin": "FLOAT",
    "forward_eps": "FLOAT", "trailing_eps": "FLOAT", "target_price": "FLOAT",
    "recommendation": "VARCHAR", "next_earnings": "VARCHAR",
}


def migrate():
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(stocks)").fetchall()}
        for name, typ in NEW_COLS.items():
            if name not in cols:
                conn.exec_driver_sql(f"ALTER TABLE stocks ADD COLUMN {name} {typ}")
        conn.commit()
    log.info("스키마 마이그레이션 완료")


def main():
    migrate()
    db = SessionLocal()
    try:
        stocks = db.query(Stock).filter(Stock.is_active == True).all()
        log.info(f"기업정보 수집 시작: {len(stocks)}종목")
        ok = 0
        for i, s in enumerate(stocks, 1):
            if fetch_company_info(db, s.ticker):
                ok += 1
            if i % 50 == 0:
                log.info(f"  진행 {i}/{len(stocks)} (성공 {ok})")
            time.sleep(0.15)
        log.info(f"기업정보 수집 완료: {ok}/{len(stocks)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
