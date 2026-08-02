"""
Risk Manager - Checks stop loss / take profit for open positions
"""

from loguru import logger
from datetime import datetime
from database.models import get_session, Trade, TradeStatus
from exchange.mexc_client import mexc
from judge.executor import close_position
import config.settings as config


async def check_stops_and_tps(app=None):
    """Check all open positions against SL / TP"""
    session = get_session()
    try:
        open_trades = (
            session.query(Trade)
            .filter(Trade.status == TradeStatus.OPEN.value)
            .all()
        )
    finally:
        session.close()

    if not open_trades:
        return

    logger.info(f"Checking {len(open_trades)} open positions for SL/TP")

    for trade in open_trades:
        try:
            ticker = await mexc.fetch_ticker(trade.symbol)
            if not ticker or not ticker.get("last"):
                continue

            current = ticker["last"]
            entry = trade.entry_price

            # Stop Loss
            if trade.stop_loss and current <= trade.stop_loss:
                logger.warning(f"SL hit: {trade.symbol} @ {current}")
                closed = await close_position(trade.symbol, reason="stop_loss")
                if closed and app and config.TG_CHAT_ID:
                    await app.bot.send_message(
                        chat_id=config.TG_CHAT_ID,
                        text=f"🛑 Stop Loss `{trade.symbol}` | {closed.pnl_pct:+.1f}%",
                        parse_mode="Markdown",
                    )
                continue

            # Take Profit
            if trade.take_profit and current >= trade.take_profit:
                logger.success(f"TP hit: {trade.symbol} @ {current}")
                closed = await close_position(trade.symbol, reason="take_profit")
                if closed and app and config.TG_CHAT_ID:
                    await app.bot.send_message(
                        chat_id=config.TG_CHAT_ID,
                        text=f"🎯 Take Profit `{trade.symbol}` | {closed.pnl_pct:+.1f}%",
                        parse_mode="Markdown",
                    )
                continue

        except Exception as e:
            logger.error(f"Error checking {trade.symbol}: {e}")
