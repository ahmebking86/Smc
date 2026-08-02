"""
Channel Listener - Reads cluster signals from shared Telegram channel
"""

from loguru import logger
from telegram import Update
from telegram.ext import ContextTypes
from judge.decision import should_enter, should_exit_positions, get_open_symbols, calculate_conviction
from judge.executor import open_position, close_position
from database.models import is_paused
import config.settings as config
from types import SimpleNamespace


def parse_signal_message(text: str) -> dict | None:
    """Parse the structured message sent by Watcher"""
    if not text or "#CLUSTER_SIGNAL" not in text:
        return None

    data = {}
    for line in text.strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            data[key.strip()] = val.strip()

    if "type" not in data or "symbol" not in data:
        return None

    try:
        return {
            "signal_type": data.get("type"),
            "token_symbol": data.get("symbol"),
            "chain": data.get("chain"),
            "wallet_count": int(data.get("wallets", 0)),
            "conviction_score": float(data.get("score", 0)),
            "total_amount_usd": float(data.get("volume", 0)),
            "token_address": data.get("token", ""),
        }
    except Exception:
        return None


async def handle_channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when a new message arrives in the shared channel"""
    if is_paused():
        return

    message = update.effective_message
    if not message or not message.text:
        return

    signal_data = parse_signal_message(message.text)
    if not signal_data:
        return

    logger.info(f"Received signal from channel: {signal_data['token_symbol']} | {signal_data['signal_type']}")

    # Convert to object-like for decision functions
    signal = SimpleNamespace(**signal_data)
    signal.id = None

    open_symbols = get_open_symbols()
    action_taken = False

    # Exit first
    to_close = should_exit_positions(signal, open_symbols)
    for sym in to_close:
        trade = await close_position(sym, reason="cluster_exit")
        if trade and config.TG_CHAT_ID:
            await context.bot.send_message(
                chat_id=config.TG_CHAT_ID,
                text=f"📕 Closed `{sym}` | {trade.pnl_pct:+.1f}% | cluster_exit",
                parse_mode="Markdown",
            )
        action_taken = True

    # Entry
    if should_enter(signal):
        symbol = f"{signal.token_symbol}/USDT"
        size = min(config.MAX_POSITION_USDT, config.CAPITAL * 0.3)
        trade = await open_position(symbol, size, signal_id=None)
        if trade and config.TG_CHAT_ID:
            await context.bot.send_message(
                chat_id=config.TG_CHAT_ID,
                text=(
                    f"📗 Opened `{symbol}`\n"
                    f"Size: ${trade.usdt_size:.1f}\n"
                    f"Score: {signal.conviction_score}"
                ),
                parse_mode="Markdown",
            )
        action_taken = True

    if not action_taken:
        logger.info(f"Signal ignored (score or conditions not met)")
