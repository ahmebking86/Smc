"""
Background monitor — checks portfolios and triggers rebalance when needed.
"""

from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

from config import MONITOR_INTERVAL, TELEGRAM_CHAT_ID
from trading.rebalance_engine import get_engine

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)

_paused: bool = False


def pause_monitor() -> None:
    global _paused
    _paused = True
    logger.info("Monitor PAUSED")


def resume_monitor() -> None:
    global _paused
    _paused = False
    logger.info("Monitor RESUMED")


async def _notify(bot: "Bot", text: str) -> None:
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="HTML")
    except Exception as e:
        logger.warning("Notify failed: %s", e)


async def monitor_loop(bot: "Bot") -> None:
    engine = get_engine()
    logger.info("Rebalance monitor started (interval=%ds)", MONITOR_INTERVAL)
    while True:
        try:
            if not _paused:
                await _tick(engine, bot)
        except Exception as e:
            logger.error("Monitor tick error: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


async def _tick(engine, bot: "Bot") -> None:
    portfolios = engine.all_active()
    if not portfolios:
        return

    for p in portfolios:
        try:
            if not engine.should_rebalance(p):
                continue
            logger.info("Rebalancing portfolio %s …", p.id[:8])
            result = await asyncio.to_thread(engine.rebalance, p)
            lines = [f"🔄 <b>إعادة توازن تلقائية</b>\nمحفظة: <code>{p.id[:8]}</code>\n"]
            if result["actions"]:
                lines.append("<b>العمليات:</b>")
                for a in result["actions"]:
                    lines.append(f"  • {a}")
            else:
                lines.append("لا توجد عمليات مطلوبة.")
            if result["errors"]:
                lines.append("\n⚠️ أخطاء:")
                for e in result["errors"][:5]:
                    lines.append(f"  • {e}")
            lines.append(f"\n💵 قيمة المحفظة: <b>{result['total_value']:.2f} USDT</b>")
            await _notify(bot, "\n".join(lines))
        except Exception as e:
            logger.error("Rebalance portfolio %s failed: %s", p.id[:8], e)
            await _notify(bot, f"❌ فشل إعادة توازن المحفظة <code>{p.id[:8]}</code>: {e}")
