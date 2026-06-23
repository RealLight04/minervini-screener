"""
DB에 누적된 분기 재무 전체를 이용해 YoY 성장률(매출·영업이익·EPS)을 재계산.
yfinance 개별 호출이 최근 5분기만 주더라도, DB엔 과거 분기가 누적돼 있어
날짜 매칭으로 3분기치 YoY까지 채울 수 있다. (네트워크 불필요)
실행: python3 scripts/recompute_yoy.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recompute_yoy")

from app.database import SessionLocal
from app.models import Fundamental, Stock


def _growth(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / abs(prev) * 100, 2)


def main():
    db = SessionLocal()
    try:
        stocks = db.query(Stock.id).all()
        updated = 0
        for (sid,) in stocks:
            rows = (
                db.query(Fundamental)
                .filter(Fundamental.stock_id == sid, Fundamental.period_type == "Q")
                .order_by(Fundamental.period_date)
                .all()
            )
            for r in rows:
                # 약 1년 전(±45일) 분기 찾기 (r보다 과거인 분기가 파트너)
                partner = None
                bestdiff = 46
                for q in rows:
                    diff = abs((r.period_date - q.period_date).days - 365)
                    if diff <= 45 and diff < bestdiff:
                        partner, bestdiff = q, diff
                if partner is None:
                    continue
                r.eps_growth_yoy = _growth(r.eps, partner.eps)
                r.revenue_growth_yoy = _growth(r.revenue, partner.revenue)
                r.operating_income_growth_yoy = _growth(r.operating_income, partner.operating_income)
                updated += 1
        db.commit()
        log.info(f"YoY 재계산 완료: {updated} 분기 갱신")
    finally:
        db.close()


if __name__ == "__main__":
    main()
