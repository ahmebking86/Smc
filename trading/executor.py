"""trading/executor.py — Order execution via ccxt (Bitget futures)."""
from __future__ import annotations

import logging
from typing import Optional

import ccxt

from config import BITGET_API_KEY, BITGET_SECRET, BITGET_PASSPHRASE
from database import (
    Trade, save_trade, get_open_trade_for_symbol,
    close_trade, get_open_trades, db_log,
)
from strategy.smc import TradeSignal
from trading.risk import position_size

logger = logging.getLogger(__name__)

# ── Exchange singleton ────────────────────────────────────────────────────────

def make_exchange() -> ccxt.bitget:
    ex = ccxt.bitget({
        "apiKey": BITGET_API_KEY,
        "secret": BITGET_SECRET,
        "password": BITGET_PASSPHRASE,
        "options": {"defaultType": "spot"},
    })
    ex.load_markets()
    return ex


_exchange: Optional[ccxt.bitget] = None


def get_exchange() -> ccxt.bitget:
    global _exchange
    if _exchange is None:
        _exchange = make_exchange()
    return _exchange


# ── Balance ───────────────────────────────────────────────────────────────────

def fetch_usdt_balance() -> float:
    ex = get_exchange()
    bal = ex.fetch_balance({"type": "spot"})
    return float(bal.get("USDT", {}).get("free", 0.0))


# ── Fetch OHLCV ───────────────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> list:
    ex = get_exchange()
    return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)


# ── Open position ─────────────────────────────────────────────────────────────

def open_position(
    symbol: str,
    signal: TradeSignal,
    risk_percent: float,
) -> Optional[Trade]:
    """Execute a market order and save to DB. Returns Trade or None on failure."""
    # Avoid duplicate positions on same symbol
    existing = get_open_trade_for_symbol(symbol)
    if existing:
        logger.info("Already have open trade for %s — skipping", symbol)
        return None

    # Spot only supports LONG (buy). Skip SHORT signals.
    if signal.side != "long":
        logger.info("Skipping SHORT signal for %s — spot trading only supports LONG", symbol)
        return None

    ex = get_exchange()

    try:
        balance = fetch_usdt_balance()
        qty = position_size(balance, risk_percent, signal.entry, signal.stop_loss)
        if qty <= 0:
            logger.warning("Position size is zero for %s — skipping", symbol)
            return None

        order = ex.create_market_order(symbol, "buy", qty)
        order_id = str(order.get("id", ""))
        actual_price = float(order.get("average") or order.get("price") or signal.entry)

        trade = Trade(
            symbol=symbol,
            side=signal.side,
            entry_price=actual_price,
            quantity=qty,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            signal_type=signal.signal_type,
            bitget_order_id=order_id,
        )
        saved = save_trade(trade)
        logger.info(
            "Opened %s %s @ %.6f  qty=%.4f  SL=%.6f  TP=%.6f",
            signal.side, symbol, actual_price, qty, signal.stop_loss, signal.take_profit,
        )
        db_log("INFO", f"Opened {signal.side} {symbol} @ {actual_price:.6f} [{signal.signal_type}]")
        return saved

    except Exception as exc:
        msg = f"Failed to open {symbol}: {exc}"
        logger.error(msg)
        db_log("ERROR", msg)
        return None


# ── Monitor & close ───────────────────────────────────────────────────────────

def monitor_open_trades() -> list[dict]:
    """Check all open trades against current price. Close if SL/TP hit."""
    ex = get_exchange()
    alerts: list[dict] = []

    for trade in get_open_trades():
        try:
            ticker = ex.fetch_ticker(trade.symbol)
            price = float(ticker["last"])
            hit = _check_sl_tp(trade, price)
            if hit:
                _close_position(ex, trade, price, hit)
                pnl = _calc_pnl(trade, price)
                alerts.append({
                    "trade": trade,
                    "reason": hit,
                    "price": price,
                    "pnl": pnl,
                })
        except Exception as exc:
            logger.error("monitor error %s: %s", trade.symbol, exc)

    return alerts


def _check_sl_tp(trade: Trade, price: float) -> Optional[str]:
    if trade.side == "long":
        if price <= trade.stop_loss:
            return "SL"
        if price >= trade.take_profit:
            return "TP"
    else:
        if price >= trade.stop_loss:
            return "SL"
        if price <= trade.take_profit:
            return "TP"
    return None


def _close_position(ex: ccxt.bitget, trade: Trade, price: float, reason: str) -> None:
    try:
        # Spot: sell the base currency to get USDT back
        ex.create_market_order(trade.symbol, "sell", trade.quantity)
    except Exception as exc:
        logger.error("Close order failed %s: %s", trade.symbol, exc)

    pnl = _calc_pnl(trade, price)
    close_trade(trade.id, price, pnl, reason)
    logger.info("Closed %s %s @ %.6f  PnL=%.4f  [%s]", trade.side, trade.symbol, price, pnl, reason)
    db_log("INFO", f"Closed {trade.side} {trade.symbol} @ {price:.6f}  PnL={pnl:.4f} [{reason}]")


def _calc_pnl(trade: Trade, exit_price: float) -> float:
    if trade.side == "long":
        return (exit_price - trade.entry_price) * trade.quantity
    else:
        return (trade.entry_price - exit_price) * trade.quantity


# ── Close all (emergency) ────────────────────────────────────────────────────

def close_all_positions() -> int:
    """Force-close every open trade. Returns count closed."""
    ex = get_exchange()
    trades = get_open_trades()
    closed = 0
    for trade in trades:
        try:
            ticker = ex.fetch_ticker(trade.symbol)
            price = float(ticker["last"])
            _close_position(ex, trade, price, "MANUAL")
            closed += 1
        except Exception as exc:
            logger.error("Emergency close failed %s: %s", trade.symbol, exc)
    return closed
