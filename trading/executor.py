"""trading/executor.py — Order execution via ccxt (Bitget spot)."""
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
from trading.risk import position_size, fixed_position_size

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
    """
    Return (or create) the exchange singleton.
    Resets the singleton on NetworkError so the next call reconnects.
    """
    global _exchange
    if _exchange is None:
        _exchange = make_exchange()
    return _exchange


def _reset_exchange() -> None:
    global _exchange
    _exchange = None


# ── Balance ───────────────────────────────────────────────────────────────────

def fetch_usdt_balance() -> float:
    try:
        ex = get_exchange()
        bal = ex.fetch_balance({"type": "spot"})
        return float(bal.get("USDT", {}).get("free", 0.0))
    except ccxt.NetworkError:
        _reset_exchange()
        raise
    except ccxt.ExchangeError:
        raise


# ── Fetch OHLCV ───────────────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> list:
    try:
        ex = get_exchange()
        return ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except ccxt.NetworkError:
        _reset_exchange()
        raise


# ── Top USDT pairs by 24h volume ──────────────────────────────────────────────

def fetch_top_usdt_pairs(n: int = 20) -> list[str]:
    """
    Return the top-N spot USDT pairs from Bitget ranked by 24-hour quote volume.
    Filters out stablecoins (USDT/USDC/BUSD/DAI base) and very new/illiquid pairs.
    Always returns normalised BASE/USDT symbols.

    BUG NOTE: ccxt tickers for Bitget spot carry the settlement suffix
    (BTC/USDT:USDT) in perpetual market mode. We load spot-only markets first
    via load_markets() with defaultType=spot and strip any stray suffix.
    """
    STABLECOIN_BASES = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP"}
    n = max(1, min(n, 50))   # hard cap at 50

    ex = get_exchange()
    try:
        tickers = ex.fetch_tickers()
    except ccxt.NetworkError:
        _reset_exchange()
        raise

    candidates: list[tuple[float, str]] = []
    for market_id, t in tickers.items():
        # Normalise symbol: strip perpetual suffix
        sym = market_id.split(":")[0].upper()
        if not sym.endswith("/USDT"):
            continue
        base = sym.split("/")[0]
        if base in STABLECOIN_BASES or base == "USDT":
            continue
        quote_vol = float(t.get("quoteVolume") or 0)
        if quote_vol <= 0:
            continue
        candidates.append((quote_vol, sym))

    candidates.sort(reverse=True)
    return [sym for _, sym in candidates[:n]]


# ── Open position ─────────────────────────────────────────────────────────────

def open_position(
    symbol: str,
    signal: TradeSignal,
    risk_percent: float,
    trade_amount_usdt: Optional[float] = None,
) -> Optional[Trade]:
    """
    Execute a spot market buy and save to DB.

    Sizing priority:
      1. trade_amount_usdt — fixed USDT amount (set via Telegram button)
      2. risk_percent      — % of balance risked on SL distance (default)

    Always fetches balance first and returns None with a warning if insufficient.
    """
    existing = get_open_trade_for_symbol(symbol)
    if existing:
        logger.info("Already have open trade for %s — skipping", symbol)
        return None

    if signal.side != "long":
        logger.info("Skipping non-long signal for %s (spot only)", symbol)
        return None

    ex = get_exchange()

    try:
        balance = fetch_usdt_balance()

        if trade_amount_usdt and trade_amount_usdt > 0:
            if balance < trade_amount_usdt:
                msg = (
                    f"Insufficient balance for {symbol}: "
                    f"need ${trade_amount_usdt:.2f}, have ${balance:.2f} USDT"
                )
                logger.warning(msg)
                db_log("WARN", msg)
                return None
            
            # For Bitget spot market buy, we MUST pass the price to calculate cost
            # or use create_market_buy_order with the cost in USDT
            sizing_note = f"fixed ${trade_amount_usdt:.2f} USDT"
            # Bitget market buy: amount is the cost in USDT
            order = ex.create_market_buy_order(symbol, trade_amount_usdt)
            qty = float(order.get('filled', 0) or order.get('amount', 0))
        else:
            qty = position_size(balance, risk_percent, signal.entry, signal.stop_loss)
            sizing_note = f"risk {risk_percent}%"

            if qty <= 0:
                logger.warning("Position size is zero for %s (%s) — skipping", symbol, sizing_note)
                return None
            
            # For Bitget market buy with specific qty, we still need to pass price
            # but it's safer to convert to cost (qty * entry) and use market_buy
            cost = qty * signal.entry
            if cost < 2.0:
                logger.warning("Cost %.2f below minimum 2 USDT — skipping %s", cost, symbol)
                return None
            order = ex.create_market_buy_order(symbol, cost)
            qty = float(order.get('filled', 0) or order.get('amount', 0))
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
            "Opened %s %s @ %.6f  qty=%.6f  SL=%.6f  TP=%.6f  [%s]",
            signal.side, symbol, actual_price, qty,
            signal.stop_loss, signal.take_profit, sizing_note,
        )
        db_log(
            "INFO",
            f"Opened {signal.side} {symbol} @ {actual_price:.6f} "
            f"[{signal.signal_type}] ({sizing_note})",
        )
        return saved

    except ccxt.NetworkError as exc:
        _reset_exchange()
        msg = f"Network error opening {symbol}: {exc}"
        logger.error(msg)
        db_log("ERROR", msg)
        return None
    except Exception as exc:
        msg = f"Failed to open {symbol}: {exc}"
        logger.error(msg)
        db_log("ERROR", msg)
        return None


# ── Monitor & close ───────────────────────────────────────────────────────────

def monitor_open_trades() -> list[dict]:
    """Check all open trades against current price. Close if SL/TP hit."""
    alerts: list[dict] = []

    for trade in get_open_trades():
        try:
            ex = get_exchange()
            ticker = ex.fetch_ticker(trade.symbol)
            price = float(ticker["last"])
            hit = _check_sl_tp(trade, price)
            if hit:
                _close_position(ex, trade, price, hit)
                pnl = _calc_pnl(trade, price)
                alerts.append({"trade": trade, "reason": hit, "price": price, "pnl": pnl})
        except ccxt.NetworkError as exc:
            _reset_exchange()
            logger.error("Network error monitoring %s: %s", trade.symbol, exc)
        except Exception as exc:
            logger.error("monitor error %s: %s", trade.symbol, exc)

    return alerts


def _check_sl_tp(trade: Trade, price: float) -> Optional[str]:
    if trade.side == "long":
        if price <= trade.stop_loss:
            return "SL"
        if price >= trade.take_profit:
            return "TP"
    return None


def _close_position(ex: ccxt.bitget, trade: Trade, price: float, reason: str) -> None:
    try:
        ex.create_market_order(trade.symbol, "sell", trade.quantity)
    except Exception as exc:
        logger.error("Close order failed %s: %s", trade.symbol, exc)

    pnl = _calc_pnl(trade, price)
    close_trade(trade.id, price, pnl, reason)
    logger.info(
        "Closed %s %s @ %.6f  PnL=%.4f  [%s]",
        trade.side, trade.symbol, price, pnl, reason,
    )
    db_log(
        "INFO",
        f"Closed {trade.side} {trade.symbol} @ {price:.6f}  PnL={pnl:.4f} [{reason}]",
    )


def _calc_pnl(trade: Trade, exit_price: float) -> float:
    # Spot: only long positions exist
    return (exit_price - trade.entry_price) * trade.quantity


# ── Close all (emergency) ─────────────────────────────────────────────────────

def close_all_positions() -> int:
    """Force-close every open trade. Returns count closed."""
    trades = get_open_trades()
    closed = 0
    for trade in trades:
        try:
            ex = get_exchange()
            ticker = ex.fetch_ticker(trade.symbol)
            price = float(ticker["last"])
            _close_position(ex, trade, price, "MANUAL")
            closed += 1
        except ccxt.NetworkError as exc:
            _reset_exchange()
            logger.error("Network error during emergency close %s: %s", trade.symbol, exc)
        except Exception as exc:
            logger.error("Emergency close failed %s: %s", trade.symbol, exc)
    return closed
