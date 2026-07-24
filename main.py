"""main.py — Entry point: starts health server, Telegram bot, and trading loop."""
from __future__ import annotations

import logging
import time

import pandas as pd

from config import SCAN_INTERVAL_SECONDS, KLINE_LIMIT
from database import init_db, get_settings, get_active_pairs
from health import start_health_server, set_status
from strategy.smc import generate_signal
from telegram_bot import (
    build_app, run_bot_in_thread,
    alert_signal, alert_trade_opened, alert_trade_closed, alert_error,
)
from trading.executor import (
    fetch_ohlcv, open_position, monitor_open_trades, fetch_usdt_balance,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

# ── Signal cooldown ───────────────────────────────────────────────────────────
# BUG FIX: without a cooldown, every scan cycle that sees the same candle
# pattern fires a duplicate alert.  5-minute minimum gap per symbol.
SIGNAL_COOLDOWN_SECONDS = 300
_last_signal_time: dict[str, float] = {}


def _is_on_cooldown(symbol: str) -> bool:
    last = _last_signal_time.get(symbol, 0.0)
    return (time.monotonic() - last) < SIGNAL_COOLDOWN_SECONDS


def _mark_signal_sent(symbol: str) -> None:
    _last_signal_time[symbol] = time.monotonic()


# ── OHLCV → DataFrame ─────────────────────────────────────────────────────────

def ohlcv_to_df(raw: list) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df.reset_index(drop=True)


# ── Single scan cycle ─────────────────────────────────────────────────────────

def scan_pair(
    symbol: str,
    risk_percent: float,
    timeframe: str,
    trade_amount_usdt=None,
) -> None:
    logger.debug("Scanning %s  tf=%s", symbol, timeframe)

    # Skip sending another alert if one just fired for this symbol
    if _is_on_cooldown(symbol):
        logger.debug("%s: on signal cooldown, skipping", symbol)
        return

    try:
        raw = fetch_ohlcv(symbol, timeframe, KLINE_LIMIT)
        if not raw or len(raw) < 50:
            logger.warning("%s: not enough candles (%d)", symbol, len(raw) if raw else 0)
            return

        df = ohlcv_to_df(raw)
        signal = generate_signal(df)

        if signal is None:
            logger.debug("%s: no signal", symbol)
            return

        # Spot only — guard against any non-long signal
        if signal.side != "long":
            logger.debug("%s: non-long signal skipped (spot only)", symbol)
            return

        logger.info("%s: signal %s %s", symbol, signal.side, signal.signal_type)

        # Attempt to open the trade FIRST
        trade = open_position(
            symbol, signal, risk_percent,
            trade_amount_usdt=trade_amount_usdt,
        )

        # Only send alert after trade attempt and mark cooldown
        _mark_signal_sent(symbol)
        alert_signal(symbol, signal)
        if trade:
            alert_trade_opened(symbol, trade)

    except Exception as exc:
        logger.error("scan_pair(%s): %s", symbol, exc)
        alert_error(f"scan_pair({symbol})", exc)


# ── Main trading loop ─────────────────────────────────────────────────────────

def trading_loop() -> None:
    logger.info("Trading loop started  interval=%ds", SCAN_INTERVAL_SECONDS)

    while True:
        try:
            cfg = get_settings()
            timeframe = cfg.timeframe or "15m"

            # Monitor existing trades for SL/TP hits
            alerts = monitor_open_trades()
            for a in alerts:
                alert_trade_closed(a["trade"], a["price"], a["pnl"], a["reason"])

            # Update health endpoint
            try:
                balance = fetch_usdt_balance()
            except Exception:
                balance = 0.0

            set_status({
                "trading_enabled": cfg.trading_enabled,
                "balance_usdt": round(balance, 2),
                "active_pairs": get_active_pairs(),
                "timeframe": timeframe,
                "trade_amount_usdt": cfg.trade_amount_usdt,
            })

            if cfg.trading_enabled:
                for pair in get_active_pairs():
                    scan_pair(
                        pair,
                        cfg.risk_percent,
                        timeframe,
                        trade_amount_usdt=cfg.trade_amount_usdt,
                    )
            else:
                logger.info("Trading paused — skipping scan")

        except Exception as exc:
            logger.exception("Unexpected error in trading loop: %s", exc)
            alert_error("trading_loop", exc)

        time.sleep(SCAN_INTERVAL_SECONDS)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=== SMC Bitget Bot starting ===")
    init_db()
    start_health_server()
    tg_app = build_app()
    run_bot_in_thread(tg_app)
    trading_loop()


if __name__ == "__main__":
    main()
