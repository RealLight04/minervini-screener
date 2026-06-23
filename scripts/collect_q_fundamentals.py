"""
분기 재무 재수집 — 영업이익·영업이익률 추가 + 날짜기반 YoY 재계산.
실행: python3 scripts/collect_q_fundamentals.py
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_q")

from app.database import SessionLocal, engine
from app.data_fetcher import fetch_and_save_fundamentals
from app.models import Stock

NEW_COLS = {
    "operating_income": "FLOAT",
    "operating_margin": "FLOAT",
    "operating_income_growth_yoy": "FLOAT",
}


def migrate():
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(fundamentals)").fetchall()}
        for name, typ in NEW_COLS.items():
            if name not in cols:
                conn.exec_driver_sql(f"ALTER TABLE fundamentals ADD COLUMN {name} {typ}")
        conn.commit()
    log.info("fundamentals 마이그레이션 완료")


def main():
    migrate()
    db = SessionLocal()
    try:
        stocks = db.query(Stock).filter(Stock.is_active == True).all()
        log.info(f"분기 재무 재수집 시작: {len(stocks)}종목")
        ok = 0
        for i, s in enumerate(stocks, 1):
            if fetch_and_save_fundamentals(db, s.ticker):
                ok += 1
            if i % 50 == 0:
                log.info(f"  진행 {i}/{len(stocks)} (성공 {ok})")
            time.sleep(0.15)
        log.info(f"분기 재무 재수집 완료: {ok}/{len(stocks)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
