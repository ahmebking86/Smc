"""
Entry point — Rebalance Portfolio Bot for BitGet via Telegram.
"""

import asyncio
import logging
import sys
import warnings

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

logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
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

    _task = asyncio.create_task(monitor_loop(app.bot))
    _task.add_done_callback(_monitor_done)
    logger.info("✅ البوت يعمل. %d محفظة محمّلة.", len(engine._portfolios))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, Conflict):
        logger.warning("⚠️ Telegram Conflict — نسخة أخرى موصولة.")
        return
    logger.error("خطأ غير معالج: %s", context.error, exc_info=context.error)


def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    build_application(app)
    app.add_error_handler(error_handler)
    logger.info("Starting polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
