import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routes import router
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # 무료 플랜(임시 디스크)에서는 스케줄러 off. 수집은 Cron Job/GitHub Actions로 분리.
    if settings.ENABLE_SCHEDULER:
        scheduler.add_job(
            _scheduled_screen,
            "cron",
            hour=settings.SCHEDULE_HOUR,
            minute=settings.SCHEDULE_MINUTE,
            id="daily_screen",
        )
        scheduler.start()
        logger.info(f"스케줄러 시작: 매일 {settings.SCHEDULE_HOUR}:{settings.SCHEDULE_MINUTE:02d} 자동 실행")
    else:
        logger.info("스케줄러 비활성화 (ENABLE_SCHEDULER=false) — 수집은 외부 cron 사용")

    yield

    if settings.ENABLE_SCHEDULER and scheduler.running:
        scheduler.shutdown()


async def _scheduled_screen():
    from app.database import SessionLocal
    from app.data_fetcher import ensure_stocks_in_db, fetch_and_save_prices, fetch_sp500_tickers
    from app.screener import run_daily_screen

    db = SessionLocal()
    try:
        stock_list = fetch_sp500_tickers()
        ensure_stocks_in_db(db, stock_list)
        for item in stock_list:
            fetch_and_save_prices(db, item["ticker"])
        run_daily_screen(db)
    finally:
        db.close()


app = FastAPI(title="Minervini Stock Screener", lifespan=lifespan)
# static 디렉토리가 비어 git에 안 올라가므로(배포 환경엔 없음) 마운트 전 보장
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(router)
