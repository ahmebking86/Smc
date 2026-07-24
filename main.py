"""main.py — Entry point: starts health server, Telegram bot, and trading loop."""
from __future__ import annotations

import logging
import time

import pandas as pd

from config import SCAN_INTERVAL_SECONDS, TIMEFRAME, KLINE_LIMIT
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


# ── OHLCV → DataFrame ─────────────────────────────────────────────────────────

def ohlcv_to_df(raw: list) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df.reset_index(drop=True)


# ── Single scan cycle ─────────────────────────────────────────────────────────

def scan_pair(symbol: str, risk_percent: float) -> None:
    logger.debug("Scanning %s", symbol)
    try:
        raw = fetch_ohlcv(symbol, TIMEFRAME, KLINE_LIMIT)
        if not raw or len(raw) < 50:
            logger.warning("%s: not enough candles (%d)", symbol, len(raw) if raw else 0)
            return

        df = ohlcv_to_df(raw)
        signal = generate_signal(df)

        if signal is None:
            logger.debug("%s: no signal", symbol)
            return

        logger.info("%s: signal %s %s", symbol, signal.side, signal.signal_type)
        alert_signal(symbol, signal)

        trade = open_position(symbol, signal, risk_percent)
        if trade:
            alert_trade_opened(symbol, trade)

    except Exception as exc:
        logger.error("scan_pair(%s): %s", symbol, exc)
        alert_error(f"scan_pair({symbol})", exc)


# ── Main trading loop ─────────────────────────────────────────────────────────

def trading_loop() -> None:
    logger.info("Trading loop started  timeframe=%s  interval=%ds", TIMEFRAME, SCAN_INTERVAL_SECONDS)

    while True:
        try:
            cfg = get_settings()

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
            })

            # Only scan for new signals if trading is enabled
            if cfg.trading_enabled:
                for pair in get_active_pairs():
                    scan_pair(pair, cfg.risk_percent)
            else:
                logger.info("Trading paused — skipping scan")

        except Exception as exc:
            logger.exception("Unexpected error in trading loop: %s", exc)
            alert_error("trading_loop", exc)

        time.sleep(SCAN_INTERVAL_SECONDS)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("=== SMC Bitget Bot starting ===")

    # 1. Init DB
    init_db()

    # 2. Health server (Railway port check)
    start_health_server()

    # 3. Telegram bot (background thread)
    tg_app = build_app()
    run_bot_in_thread(tg_app)

    # 4. Trading loop (main thread — blocks forever)
    trading_loop()


if __name__ == "__main__":
    main()
