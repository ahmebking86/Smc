"""
Decision Engine - Calculates final action
"""

from loguru import logger
import config.settings as config
from database.models import get_session, Trade, TradeStatus


def calculate_conviction(signal) -> float:
    """Recalculate or adjust conviction if needed"""
    base = signal.conviction_score or 0
    # Bonus for more wallets
    if signal.wallet_count >= 5:
        base = min(100, base + 10)
    if signal.wallet_count >= 7:
        base = min(100, base + 8)
    return base


def should_enter(signal) -> bool:
    if signal.signal_type != "cluster_accumulation":
        return False
    score = calculate_conviction(signal)
    return score >= config.MIN_CONVICTION_SCORE


def should_exit_positions(signal, open_symbols: list) -> list:
    """Return list of symbols that should be closed"""
    if signal.signal_type != "cluster_exit":
        return []
    score = calculate_conviction(signal)
    if score < config.MIN_CONVICTION_SCORE:
        return []

    symbol = signal.token_symbol
    if not symbol:
        return []

    # Match against open positions (simple contains check)
    to_close = []
    for open_sym in open_symbols:
        if symbol.upper() in open_sym.upper():
            to_close.append(open_sym)
    return to_close


def get_open_symbols() -> list:
    session = get_session()
    try:
        trades = (
            session.query(Trade)
            .filter(Trade.status == TradeStatus.OPEN.value)
            .all()
        )
        return [t.symbol for t in trades]
    finally:
        session.close()
