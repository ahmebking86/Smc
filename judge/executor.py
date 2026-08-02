"""
Executor - Opens and closes positions on MEXC Spot
"""

from loguru import logger
from datetime import datetime
from database.models import get_session, Trade, TradeStatus
from exchange.mexc_client import mexc
import config.settings as config


async def open_position(symbol: str, usdt_size: float, signal_id: int = None):
    """Open a spot long position"""
    if usdt_size > config.MAX_POSITION_USDT:
        usdt_size = config.MAX_POSITION_USDT

    ticker = await mexc.fetch_ticker(symbol)
    if not ticker or not ticker.get("last"):
        logger.error(f"Cannot open {symbol}: no price")
        return None

    price = ticker["last"]
    quantity = usdt_size / price

    order = await mexc.create_market_buy(symbol, quantity)
    if not order:
        return None

    stop_loss = price * (1 - config.STOP_LOSS_PCT / 100)
    take_profit = price * (1 + config.TAKE_PROFIT_PCT / 100)

    session = get_session()
    try:
        trade = Trade(
            signal_id=signal_id,
            symbol=symbol,
            side="buy",
            entry_price=price,
            quantity=quantity,
            usdt_size=usdt_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status=TradeStatus.OPEN.value,
            mode=config.MODE,
        )
        session.add(trade)
        session.commit()
        logger.success(f"Opened {symbol} | size=${usdt_size:.1f} @ {price}")
        return trade
    except Exception as e:
        logger.error(f"Failed to record trade: {e}")
        session.rollback()
        return None
    finally:
        session.close()


async def close_position(symbol: str, reason: str = "signal"):
    session = get_session()
    try:
        trade = (
            session.query(Trade)
            .filter(
                Trade.symbol == symbol,
                Trade.status == TradeStatus.OPEN.value,
            )
            .first()
        )
        if not trade:
            logger.warning(f"No open trade for {symbol}")
            return None

        ticker = await mexc.fetch_ticker(symbol)
        exit_price = ticker["last"] if ticker else trade.entry_price

        order = await mexc.create_market_sell(symbol, trade.quantity)
        if not order and config.MODE == "real":
            logger.error(f"Sell order failed for {symbol}")
            return None

        pnl = (exit_price - trade.entry_price) * trade.quantity
        pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price) * 100

        trade.exit_price = exit_price
        trade.pnl = round(pnl, 4)
        trade.pnl_pct = round(pnl_pct, 2)
        trade.status = TradeStatus.CLOSED.value
        trade.exit_reason = reason
        trade.exit_time = datetime.utcnow()
        session.commit()

        logger.success(
            f"Closed {symbol} | PnL={pnl:.2f}$ ({pnl_pct:+.1f}%) | {reason}"
        )
        return trade
    except Exception as e:
        logger.error(f"close_position error: {e}")
        session.rollback()
        return None
    finally:
        session.close()
