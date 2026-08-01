"""
Entry point — MEXC portfolio rebalancing bot via Telegram.
"""

import asyncio
import logging
import sys
import warnings
import signal

from telegram.warnings import PTBUserWarning
warnings.filterwarnings("ignore", category=PTBUserWarning)

from telegram.error import Conflict
from telegram.ext import Application, ContextTypes

from config import TELEGRAM_TOKEN
from bot.handlers import build_application
import database.db as db
from trading.rebalance_engine import get_engine
from trading.monitor import monitor_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

_monitor_task: asyncio.Task | None = None


async def post_init(app: Application) -> None:
    global _monitor_task
    try:
        db.init_db()
    except Exception as m_exc:
        logger.critical("❌ فشل تهيئة قاعدة البيانات: %s", m_exc)
        raise

    engine = get_engine()
    try:
        engine.load_from_db()
    except Exception as exc:
        logger.critical("❌ فشل تحميل المحافظ: %s", exc)
        raise

    def _monitor_done(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.critical("❌ monitor_loop انهار: %s", exc)

    _monitor_task = asyncio.create_task(monitor_loop(app.bot))
    _monitor_task.add_done_callback(_monitor_done)
    logger.info("✅ البوت يعمل. %d محفظة محمّلة.", len(engine._portfolios))


async def post_shutdown(app: Application) -> None:
    global _monitor_task
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
        logger.info("Monitor stopped cleanly.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        logger.warning("⚠️ Telegram Conflict — نسخة أخرى موصولة. تجاهل.")
        return
    logger.error("خطأ غير معالج: %s", err, exc_info=err)


def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    build_application(app)
    app.add_error_handler(error_handler)
    logger.info("Starting polling…")
    # drop_pending_updates=True prevents old updates from causing issues on restart
    app.run_polling(drop_pending_updates=True, close_loop=False)


if __name__ == "__main__":
    main()
