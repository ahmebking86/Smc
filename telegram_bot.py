"""telegram_bot.py — Telegram control panel + alert sender."""
from __future__ import annotations

import html
import logging
import threading
from typing import Optional

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, ConversationHandler, filters,
)
from telegram.error import TelegramError

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from database import (
    get_settings, set_trading_enabled, set_risk_percent,
    set_active_pairs, get_active_pairs, get_open_trades, get_today_stats,
)

logger = logging.getLogger(__name__)

# Conversation states
AWAIT_RISK, AWAIT_PAIRS = range(2)

# ── Singleton bot for sending alerts ─────────────────────────────────────────

_bot: Optional[Bot] = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


def send_alert(text: str) -> None:
    """Send a message to the admin chat (blocking, called from trading loop)."""
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(
            get_bot().send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode="HTML",
            )
        )
        loop.close()
    except TelegramError as exc:
        logger.error("Telegram send failed: %s", exc)


# ── Auth guard ────────────────────────────────────────────────────────────────

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id != TELEGRAM_CHAT_ID:
            await update.message.reply_text("⛔ Unauthorized")
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


# ── /start  /menu ─────────────────────────────────────────────────────────────

@admin_only
async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_settings()
    trades = get_open_trades()
    stats = get_today_stats()
    mode = "🟢 Active" if cfg.trading_enabled else "🔴 Paused"

    try:
        from trading.executor import fetch_usdt_balance
        balance = fetch_usdt_balance()
        balance_str = f"${balance:.2f}"
    except Exception:
        balance_str = "N/A"

    text = (
        f"🤖 <b>SMC Bitget Bot</b>\n\n"
        f"Status:   <b>{mode}</b>\n"
        f"Balance:  <b>{balance_str} USDT</b>\n"
        f"Risk:     <b>{cfg.risk_percent}%</b>\n"
        f"Pairs:    <b>{cfg.active_pairs}</b>\n\n"
        f"Open trades:  <b>{len(trades)}</b>\n"
        f"Today PnL:    <b>${stats['pnl']:.2f}</b> ({stats['count']} trades)\n"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Status",    callback_data="status"),
            InlineKeyboardButton("⚡ Toggle",    callback_data="toggle"),
        ],
        [
            InlineKeyboardButton("💰 Set Risk",  callback_data="setrisk"),
            InlineKeyboardButton("🔗 Pairs",     callback_data="pairs"),
        ],
        [
            InlineKeyboardButton("🚨 Close ALL", callback_data="closeall"),
        ],
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ── /status ───────────────────────────────────────────────────────────────────

@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    trades = get_open_trades()
    if not trades:
        msg = "📭 No open trades."
    else:
        lines = ["📈 <b>Open Trades</b>\n"]
        for t in trades:
            lines.append(
                f"• <b>{t.symbol}</b> [{t.side.upper()}]\n"
                f"  Entry: {t.entry_price:.6f}\n"
                f"  SL: <b>{t.stop_loss:.6f}</b>  TP: <b>{t.take_profit:.6f}</b>\n"
                f"  Signal: {t.signal_type}\n"
            )
        msg = "\n".join(lines)

    target = update.message or update.callback_query.message
    await target.reply_text(msg, parse_mode="HTML")


# ── /toggle ───────────────────────────────────────────────────────────────────

@admin_only
async def cmd_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_settings()
    new_state = not cfg.trading_enabled
    set_trading_enabled(new_state)
    label = "🟢 Enabled" if new_state else "🔴 Paused"
    msg = f"Trading is now <b>{label}</b>"
    target = update.message or update.callback_query.message
    await target.reply_text(msg, parse_mode="HTML")


# ── /setrisk ──────────────────────────────────────────────────────────────────

@admin_only
async def cmd_setrisk_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    cfg = get_settings()
    target = update.message or update.callback_query.message
    await target.reply_text(
        f"💰 Current risk: <b>{cfg.risk_percent}%</b>\n\nSend new risk % (e.g. <code>1.5</code>):",
        parse_mode="HTML",
    )
    return AWAIT_RISK


async def cmd_setrisk_recv(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip())
        if not 0.1 <= val <= 10:
            raise ValueError
        set_risk_percent(val)
        await update.message.reply_text(f"✅ Risk set to <b>{val}%</b>", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("❌ Invalid value. Enter a number between 0.1 and 10.")
    return ConversationHandler.END


# ── /pairs ────────────────────────────────────────────────────────────────────

@admin_only
async def cmd_pairs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    pairs = get_active_pairs()
    target = update.message or update.callback_query.message
    await target.reply_text(
        f"🔗 <b>Active pairs:</b>\n{chr(10).join(pairs)}\n\n"
        f"Use /addpair SYMBOL or /removepair SYMBOL\nExample: <code>/addpair BTC/USDT:USDT</code>",
        parse_mode="HTML",
    )


@admin_only
async def cmd_addpair(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /addpair BTC/USDT:USDT")
        return
    symbol = ctx.args[0].upper()
    pairs = get_active_pairs()
    if symbol not in pairs:
        pairs.append(symbol)
        set_active_pairs(pairs)
        await update.message.reply_text(f"✅ Added <b>{html.escape(symbol)}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ Already in list: {html.escape(symbol)}")


@admin_only
async def cmd_removepair(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("Usage: /removepair BTC/USDT:USDT")
        return
    symbol = ctx.args[0].upper()
    pairs = get_active_pairs()
    if symbol in pairs:
        pairs.remove(symbol)
        set_active_pairs(pairs)
        await update.message.reply_text(f"✅ Removed <b>{html.escape(symbol)}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text(f"⚠️ Not in list: {html.escape(symbol)}")


# ── /closeall ─────────────────────────────────────────────────────────────────

@admin_only
async def cmd_closeall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    target = update.message or update.callback_query.message
    await target.reply_text("🚨 Closing all positions…")
    try:
        from trading.executor import close_all_positions
        count = close_all_positions()
        await target.reply_text(f"✅ Closed <b>{count}</b> position(s).", parse_mode="HTML")
    except Exception as exc:
        await target.reply_text(f"❌ Error: {html.escape(str(exc))}", parse_mode="HTML")


# ── Inline button router ──────────────────────────────────────────────────────

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "status":
        await cmd_status(update, ctx)
    elif data == "toggle":
        await cmd_toggle(update, ctx)
    elif data == "pairs":
        await cmd_pairs(update, ctx)
    elif data == "closeall":
        await cmd_closeall(update, ctx)
    elif data == "setrisk":
        await cmd_setrisk_start(update, ctx)


# ── Alert formatters ──────────────────────────────────────────────────────────

def alert_signal(symbol: str, signal) -> None:
    send_alert(
        f"📡 <b>SMC Signal — {html.escape(symbol)}</b>\n\n"
        f"Side:        <b>{signal.side.upper()}</b>\n"
        f"Entry:       <b>{signal.entry:.6f}</b>\n"
        f"Stop Loss:   <b>{signal.stop_loss:.6f}</b>\n"
        f"Take Profit: <b>{signal.take_profit:.6f}</b>\n"
        f"Type:        <b>{signal.signal_type}</b>\n\n"
        f"<i>{html.escape(signal.reason)}</i>"
    )


def alert_trade_opened(symbol: str, trade) -> None:
    send_alert(
        f"✅ <b>Trade Opened — {html.escape(symbol)}</b>\n\n"
        f"Side:        <b>{trade.side.upper()}</b>\n"
        f"Entry:       <b>{trade.entry_price:.6f}</b>\n"
        f"Stop Loss:   <b>{trade.stop_loss:.6f}</b>\n"
        f"Take Profit: <b>{trade.take_profit:.6f}</b>\n"
        f"Qty:         <b>{trade.quantity}</b>\n"
        f"Signal:      <b>{trade.signal_type}</b>"
    )


def alert_trade_closed(trade, price: float, pnl: float, reason: str) -> None:
    emoji = "🟢" if pnl >= 0 else "🔴"
    send_alert(
        f"{emoji} <b>Trade Closed [{reason}] — {html.escape(trade.symbol)}</b>\n\n"
        f"Side:   <b>{trade.side.upper()}</b>\n"
        f"Entry:  {trade.entry_price:.6f}\n"
        f"Exit:   <b>{price:.6f}</b>\n"
        f"PnL:    <b>${pnl:.4f}</b>"
    )


def alert_error(context: str, exc: Exception) -> None:
    send_alert(
        f"⚠️ <b>Bot Error</b>\n\n"
        f"Where: {html.escape(context)}\n"
        f"Error: {html.escape(str(exc))}"
    )


# ── Build & run application ───────────────────────────────────────────────────

def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    risk_conv = ConversationHandler(
        entry_points=[
            CommandHandler("setrisk", cmd_setrisk_start),
        ],
        states={AWAIT_RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_setrisk_recv)]},
        fallbacks=[],
    )

    app.add_handler(CommandHandler(["start", "menu"], cmd_menu))
    app.add_handler(CommandHandler("status",     cmd_status))
    app.add_handler(CommandHandler("toggle",     cmd_toggle))
    app.add_handler(CommandHandler("pairs",      cmd_pairs))
    app.add_handler(CommandHandler("addpair",    cmd_addpair))
    app.add_handler(CommandHandler("removepair", cmd_removepair))
    app.add_handler(CommandHandler("closeall",   cmd_closeall))
    app.add_handler(risk_conv)
    app.add_handler(CallbackQueryHandler(button_handler))

    return app


def run_bot_in_thread(app: Application) -> None:
    """Run the Telegram bot in a background thread (polling)."""
    def _run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(app.run_polling(drop_pending_updates=True))

    t = threading.Thread(target=_run, name="telegram-bot", daemon=True)
    t.start()
    logger.info("Telegram bot started")
