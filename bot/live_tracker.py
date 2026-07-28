"""
Manages per-session live Telegram tracking messages.
Each active session can have one live-updating message (edited every 30s).
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# session_id → asyncio.Task
_tasks:    dict[str, asyncio.Task]       = {}
# session_id → (chat_id, message_id)
_messages: dict[str, tuple[int, int]]   = {}

REFRESH_INTERVAL = 30   # seconds between auto-updates


def _live_kb(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 تحديث الآن",  callback_data=f"live_refresh_{session_id}"),
        InlineKeyboardButton("⏹ إيقاف البث",   callback_data=f"live_stop_{session_id}"),
    ]])


async def _render_message(session_id: str, bot: Bot) -> None:
    """Fetch fresh data and edit the live message."""
    from trading.grid_engine import get_engine
    from database import db
    from bot.chart_builder import build_grid_chart

    info = _messages.get(session_id)
    if not info:
        return

    chat_id, msg_id = info
    engine  = get_engine()
    session = engine.get_session(session_id)
    if not session:
        return

    try:
        price   = await asyncio.to_thread(engine.client.get_price, session.config.symbol)
        pnl     = await asyncio.to_thread(db.session_total_pnl, session.id)
        fills   = sum(1 for l in session.levels if l.status == "filled")
        invested = session.config.entry_amount or 1
        pnl_pct  = pnl / invested * 100

        text = build_grid_chart(session, price, pnl, pnl_pct, fills)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_live_kb(session_id),
        )
    except Exception as e:
        err_str = str(e)
        if "Message is not modified" in err_str:
            pass  # طبيعي — السعر ما تغيرش
        elif any(k in err_str.lower() for k in (
            "message to edit not found",
            "message can't be edited",
            "bad request",
            "message_id_invalid",
        )):
            # الرسالة اتمسحت أو انتهت — وقّف التتبع تلقائياً عشان اللوج ما يتعبيش
            logger.info(
                "live_tracker: رسالة محذوفة، إيقاف التتبع تلقائياً [%s]",
                session_id[:8],
            )
            _messages.pop(session_id, None)
            task = _tasks.pop(session_id, None)
            if task:
                task.cancel()
        else:
            logger.warning("live_tracker render [%s]: %s", session_id[:8], e)


async def _update_loop(session_id: str, bot: Bot) -> None:
    """Background loop that updates the live message every REFRESH_INTERVAL seconds."""
    try:
        while session_id in _messages:
            await asyncio.sleep(REFRESH_INTERVAL)
            await _render_message(session_id, bot)
    except asyncio.CancelledError:
        pass


async def start(session, bot: Bot, chat_id: int) -> None:
    """Send a new live tracking message and start the update loop."""
    from trading.grid_engine import get_engine
    from database import db
    from bot.chart_builder import build_grid_chart

    # Stop any existing tracker for this session
    await stop(session.id, bot, silent=True)

    engine   = get_engine()
    price    = await asyncio.to_thread(engine.client.get_price, session.config.symbol)
    pnl      = await asyncio.to_thread(db.session_total_pnl, session.id)
    fills    = sum(1 for l in session.levels if l.status == "filled")
    pnl_pct  = pnl / (session.config.entry_amount or 1) * 100

    text = build_grid_chart(session, price, pnl, pnl_pct, fills)
    msg  = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=_live_kb(session.id),
    )
    _messages[session.id] = (chat_id, msg.message_id)
    task = asyncio.create_task(_update_loop(session.id, bot))
    _tasks[session.id]   = task
    logger.info("Live tracker started for session %s", session.id[:8])


async def refresh(session_id: str, bot: Bot) -> bool:
    """Trigger an immediate refresh (called from inline button)."""
    if session_id not in _messages:
        return False
    await _render_message(session_id, bot)
    return True


async def stop(session_id: str, bot: Bot, silent: bool = False) -> None:
    """Cancel the update loop and remove interactive buttons from the message."""
    task = _tasks.pop(session_id, None)
    if task:
        task.cancel()

    info = _messages.pop(session_id, None)
    if info and not silent:
        try:
            await bot.edit_message_reply_markup(
                chat_id=info[0], message_id=info[1], reply_markup=None
            )
        except Exception:
            pass
    logger.info("Live tracker stopped for session %s", session_id[:8])


def is_active(session_id: str) -> bool:
    return session_id in _messages
