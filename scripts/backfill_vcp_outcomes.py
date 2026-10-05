"""
기존 VCP 돌파(broke_out) 이벤트에 국면 스냅샷·실현 결과를 소급 채우는 1회성 스크립트.

screening_results가 90일 후 정리되므로(prune_old_history), regime_at_breakout은
지금 다 남아있을 때만 복원 가능하다. 한 번 지나가면 영영 못 채운다.
outcome(손절/목표/타임아웃)은 매일 스크리닝 때도 갱신되지만, 이 스크립트를 먼저
돌리면 이미 쌓인 이력에 바로 값이 채워진다.

실행: python scripts/backfill_vcp_outcomes.py
      python scripts/backfill_vcp_outcomes.py --recompute   # 손절·목표 규칙을 바꾼 뒤 결과를 전부 다시 계산
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_vcp_outcomes")

from app.database import SessionLocal, init_db
from app.models import Stock, VCPEvent
from app.screener import compute_market_breadth, update_breakout_outcomes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recompute", action="store_true", help="이미 확정된 outcome도 지우고 현재 규칙으로 재계산")
    args = ap.parse_args()
    init_db()  # 새 컬럼(regime_at_breakout 등) 자가치유 ALTER
    db = SessionLocal()
    try:
        events = db.query(VCPEvent).filter(
            VCPEvent.status == "broke_out", VCPEvent.regime_at_breakout.is_(None)
        ).all()
        stock_market = dict(db.query(Stock.id, Stock.market).all())
        regime_cache = {}
        filled = 0
        for ev in events:
            if not ev.breakout_date:
                continue
            mk = stock_market.get(ev.stock_id) or "US"
            key = (mk, ev.breakout_date)
            if key not in regime_cache:
                regime_cache[key] = compute_market_breadth(db, ev.breakout_date, mk)
            reg = regime_cache[key]
            if reg.get("available"):
                ev.regime_at_breakout = reg["regime"]
                ev.pct_above_200_at_breakout = reg["pct_above_200"]
                filled += 1
        db.commit()
        log.info(f"국면 스냅샷 채움: {filled}/{len(events)}건")

        outcome_stats = update_breakout_outcomes(db, date.today(), recompute=args.recompute)
        log.info(f"결과 확정: {outcome_stats}")
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
