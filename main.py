"""
Cluster Judge - Main Entry Point
Reads signals, decides, and executes on MEXC Spot
"""

import asyncio
from loguru import logger
from telegram.ext import Application
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config.settings as config
from database.models import init_db, is_paused, set_paused
from judge.processor import process_new_signals
from exchange.mexc_client import mexc
from tg_bot.handlers import setup_handlers
from utils.logger import setup_logger


async def main():
    setup_logger(config.LOG_LEVEL)
    logger.info("🚀 Starting Cluster Judge...")
    logger.info(f"Mode: {config.MODE} | Capital: ${config.CAPITAL}")

    init_db()
    logger.info("Database initialized")

    if config.PAUSE_ON_START:
        set_paused(True)
        logger.info("PAUSE_ON_START=true → system paused")

    await mexc.load_markets()

    if not config.TG_TOKEN:
        logger.error("TG_TOKEN missing!")
        return

    app = Application.builder().token(config.TG_TOKEN).build()
    setup_handlers(app)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        process_new_signals,
        "interval",
        seconds=30,
        id="judge_cycle",
        max_instances=1,
        kwargs={"app": app},
    )
    scheduler.start()

    async def delayed_start():
        await asyncio.sleep(8)
        await process_new_signals(app)

    asyncio.create_task(delayed_start())

    logger.info("Telegram bot starting (Judge)...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    stop_event = asyncio.Event()
    await stop_event.wait()


if __name__ == "__main__":
    asyncio.run(main())
