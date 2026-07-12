"""
로컬 상시구동 PC에서 Windows 작업 스케줄러가 호출하는 진입점.
각 시장 마감 10분 후 실행되도록 예약해두면, GitHub Actions의 스케줄 지연(20~60분+) 없이
로컬 screener.db를 즉시 갱신한다(같은 PC에서 서버가 그 DB를 바로 서빙하므로 반영도 즉시).

실행: python scripts/local_refresh.py --mode kr
      python scripts/local_refresh.py --mode us --season edt
      python scripts/local_refresh.py --mode us --season est

--season은 미국장 전용: 뉴욕이 지금 그 서머타임 상태가 아니면 조용히 skip한다.
(EDT/EST 두 슬롯을 모두 매일 걸어두고, 스크립트가 스스로 판단해 하나만 실제로 돈다 —
 .github/workflows/daily-refresh.yml의 'Decide run mode' 스텝과 동일한 방식)
"""
import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
PY = sys.executable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("local_refresh")


def ny_is_edt() -> bool:
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.dst().total_seconds() > 0


def run_step(*args) -> None:
    log.info(f"실행: {' '.join(args)}")
    subprocess.run([PY, *args], cwd=ROOT, check=True)


def run_step_soft(*args) -> None:
    """실패해도 파이프라인 전체는 계속 진행(EPS 백필처럼 선택적인 단계용)."""
    log.info(f"실행(실패 허용): {' '.join(args)}")
    subprocess.run([PY, *args], cwd=ROOT, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["kr", "us"], required=True)
    parser.add_argument("--season", choices=["edt", "est"], default=None,
                         help="mode=us일 때만 사용. 뉴욕이 이 서머타임 상태가 아니면 skip.")
    args = parser.parse_args()

    if args.mode == "us" and args.season:
        is_edt = ny_is_edt()
        wanted_edt = args.season == "edt"
        if is_edt != wanted_edt:
            log.info(f"skip — 뉴욕은 현재 {'EDT' if is_edt else 'EST'}, 이 슬롯은 {args.season.upper()}용")
            return 0

    if args.mode == "kr":
        run_step("scripts/collect_kr.py")
    else:
        run_step("scripts/daily_update.py")
        run_step_soft("scripts/backfill_eps.py", "--limit", "25")
        run_step("scripts/recompute_yoy.py")

    log.info("완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
