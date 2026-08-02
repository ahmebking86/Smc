"""
Telegram Handlers for Judge Bot
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from loguru import logger
from database.models import get_session, Trade, TradeStatus, is_paused, set_paused, ClusterSignal
from exchange.mexc_client import mexc
import config.settings as config


def is_admin(user_id: int) -> bool:
    return user_id in config.TG_ADMIN_IDS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("غير مصرح.")
        return

    keyboard = [
        [
            InlineKeyboardButton("📊 الحالة", callback_data="status"),
            InlineKeyboardButton("📈 المراكز", callback_data="positions"),
        ],
        [
            InlineKeyboardButton("⏸ إيقاف", callback_data="pause"),
            InlineKeyboardButton("▶️ تشغيل", callback_data="resume"),
        ],
        [
            InlineKeyboardButton("📄 Paper", callback_data="mode_paper"),
            InlineKeyboardButton("💰 Real", callback_data="mode_real"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚖️ *Cluster Judge*\nاختر أمر:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return

    data = query.data

    if data == "status":
        paused = is_paused()
        session = get_session()
        try:
            open_count = session.query(Trade).filter_by(status=TradeStatus.OPEN.value).count()
            closed_count = session.query(Trade).filter_by(status=TradeStatus.CLOSED.value).count()
            new_signals = session.query(ClusterSignal).filter_by(status="new").count()
        finally:
            session.close()

        text = (
            f"⚖️ *Judge Status*\n\n"
            f"الوضع: `{'⏸ متوقف' if paused else '▶️ يعمل'}`\n"
            f"Mode: `{config.MODE}`\n"
            f"رأس المال: `${config.CAPITAL}`\n"
            f"مراكز مفتوحة: `{open_count}`\n"
            f"صفقات مغلقة: `{closed_count}`\n"
            f"إشارات جديدة: `{new_signals}`\n"
            f"حد الصفقة: `${config.MAX_POSITION_USDT}`"
        )
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "positions":
        session = get_session()
        try:
            trades = (
                session.query(Trade)
                .filter_by(status=TradeStatus.OPEN.value)
                .all()
            )
        finally:
            session.close()

        if not trades:
            await query.edit_message_text("لا توجد مراكز مفتوحة.")
            return

        lines = []
        for t in trades:
            lines.append(
                f"`{t.symbol}` | entry:{t.entry_price:.6g} | "
                f"size:${t.usdt_size:.1f}"
            )
        text = "📈 *Open Positions*\n\n" + "\n".join(lines)
        await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "pause":
        set_paused(True)
        await query.edit_message_text("⏸ تم إيقاف النظام.")

    elif data == "resume":
        set_paused(False)
        await query.edit_message_text("▶️ تم تشغيل النظام.")

    elif data == "mode_paper":
        await query.edit_message_text(
            "لتغيير الوضع إلى Paper عدّل المتغير MODE=paper في Railway وأعد النشر."
        )

    elif data == "mode_real":
        await query.edit_message_text(
            "⚠️ لتفعيل Real غيّر MODE=real في Railway.\nتأكد إن المفاتيح صح والوضع Paper اشتغل كويس الأول."
        )


def setup_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
