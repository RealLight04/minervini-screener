"""
한국 종목 섹터를 yfinance .info에서 백필 (FDR Dept는 업종이 아니라서).
실행: python3 scripts/collect_kr_sectors.py
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("collect_kr_sectors")

import yfinance as yf

from app.database import SessionLocal
from app.models import Stock

# yfinance 영문 섹터 → 한글
SECTOR_KO = {
    "Technology": "기술/IT", "Financial Services": "금융", "Healthcare": "헬스케어",
    "Consumer Cyclical": "경기소비재", "Consumer Defensive": "필수소비재",
    "Industrials": "산업재", "Basic Materials": "소재", "Energy": "에너지",
    "Communication Services": "커뮤니케이션", "Utilities": "유틸리티", "Real Estate": "부동산",
}

# FDR이 sector 자리에 넣어둔 KOSDAQ '소속부'(상장 등급)는 섹터가 아니므로 정리 대상.
# yfinance가 섹터를 못 주는 종목은 이 값이 남아 '주도 섹터/테마'를 오염시킨다.
BOARD_DIVISIONS = {
    "우량기업부", "중견기업부", "벤처기업부", "기술성장기업부",
    "외국기업(소속부없음)", "코스닥", "코스피",
}


def main():
    db = SessionLocal()
    try:
        stocks = db.query(Stock).filter(Stock.market.in_(["KOSPI", "KOSDAQ"]), Stock.is_active == True).all()
        log.info(f"한국 섹터 백필: {len(stocks)}종목")
        ok = 0
        for i, s in enumerate(stocks, 1):
            try:
                sec = yf.Ticker(s.ticker).info.get("sector")
                if sec:
                    s.sector = SECTOR_KO.get(sec, sec)
                    ok += 1
                elif s.sector in BOARD_DIVISIONS:
                    # 실섹터를 못 받았는데 소속부 값이 남아있으면 '미분류'(NULL)로 정리
                    s.sector = None
            except Exception as e:
                log.warning(f"{s.ticker} 실패: {e}")
            if i % 50 == 0:
                db.commit()
                log.info(f"  진행 {i}/{len(stocks)} (성공 {ok})")
            time.sleep(0.15)
        # 혹시 남은 소속부 값 일괄 정리 (실패/예외로 건너뛴 종목 포함)
        cleared = (
            db.query(Stock)
            .filter(Stock.market.in_(["KOSPI", "KOSDAQ"]), Stock.sector.in_(BOARD_DIVISIONS))
            .update({Stock.sector: None}, synchronize_session=False)
        )
        db.commit()
        log.info(f"한국 섹터 백필 완료: {ok}/{len(stocks)} (소속부 정리 {cleared}건)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
