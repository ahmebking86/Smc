"""trading/risk.py — Position sizing."""
from __future__ import annotations

from typing import Optional


def position_size(
    balance_usdt: float,
    risk_percent: float,
    entry: float,
    stop_loss: float,
) -> float:
    """
    Spot position sizing based on risk percentage (no leverage).

    risk_amount = balance × (risk_percent / 100)
    sl_distance = |entry - stop_loss|
    size        = risk_amount / sl_distance   [in base currency]
    """
    if entry <= 0 or stop_loss <= 0:
        return 0.0
    sl_distance = abs(entry - stop_loss)
    if sl_distance == 0:
        return 0.0
    risk_amount = balance_usdt * (risk_percent / 100)
    size = risk_amount / sl_distance
    return round(size, 6)


def fixed_position_size(amount_usdt: float, entry: float) -> float:
    """
    Spot position sizing using a fixed USDT amount.

    size = amount_usdt / entry   [in base currency]
    """
    if entry <= 0 or amount_usdt <= 0:
        return 0.0
    return round(amount_usdt / entry, 6)
