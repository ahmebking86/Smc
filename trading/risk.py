"""trading/risk.py — Position sizing."""
from __future__ import annotations


def position_size(
    balance_usdt: float,
    risk_percent: float,
    entry: float,
    stop_loss: float,
) -> float:
    """
    Spot position sizing (no leverage).

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
