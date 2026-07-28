"""
Entry point — starts the Telegram bot and background monitor.
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
from trading.grid_engine import get_engine
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
    """Called after the bot is initialised — start the monitor task."""
    # إنشاء الجداول + تطبيق migrations تلقائياً عند كل بدء تشغيل (idempotent)
    try:
        db.init_db()
    except Exception as m_exc:
        logger.critical("❌ فشل تهيئة قاعدة البيانات: %s", m_exc)
        raise
    engine = get_engine()
    try:
        engine.load_from_db()
    except Exception as exc:
        logger.critical(
            "❌ فشل تحميل الجلسات من قاعدة البيانات عند البدء: %s\n"
            "   تحقق من متغير DATABASE_URL في Railway.",
            exc,
        )
        raise  # أوقف البوت — لا فائدة من التشغيل بقاعدة بيانات معطلة
    def _monitor_done(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.critical("❌ monitor_loop انهار: %s — البوت لن يتابع الجلسات!", exc)

    _task = asyncio.create_task(monitor_loop(app.bot))
    _task.add_done_callback(_monitor_done)
    logger.info("✅ البوت يعمل. %d شبكة محملة من قاعدة البيانات.", len(engine._sessions))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    FIX: بدون هذا الـ handler، أي خطأ غير معالج (مثل Conflict عند إعادة تشغيل
    الـ container) يتسبب في تعطل البوت وإعادة تشغيله في حلقة لا نهائية.
    Conflict يحدث عندما تكون نسختان من البوت شغّالتين في نفس الوقت لثوانٍ
    (مثلاً عند إعادة نشر Railway) — نتجاهله ونتركه يُعيد الاتصال تلقائياً.
    """
    if isinstance(context.error, Conflict):
        logger.warning(
            "⚠️ Telegram Conflict — نسخة أخرى من البوت موصولة حالياً. "
            "ستُعيد المكتبة المحاولة تلقائياً."
        )
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
