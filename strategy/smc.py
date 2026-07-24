"""strategy/smc.py — Smart Money Concepts analysis (Spot LONG only).

Detects:
  - Swing Highs / Swing Lows
  - BOS  (Break of Structure)
  - CHoCH (Change of Character)
  - OB   (Order Block)
  - FVG  (Fair Value Gap)

Returns a TradeSignal(side='long') or None.
SHORT signals are never returned — this bot trades spot only.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str   # 'high' | 'low'


@dataclass
class StructureEvent:
    kind: str        # 'BOS_bull' | 'BOS_bear' | 'CHoCH_bull' | 'CHoCH_bear'
    price: float     # broken level
    bar_index: int


@dataclass
class OrderBlock:
    kind: str        # 'bull' | 'bear'
    top: float
    bottom: float
    bar_index: int


@dataclass
class FVG:
    kind: str        # 'bull' | 'bear'
    top: float
    bottom: float
    bar_index: int   # middle candle index


@dataclass
class TradeSignal:
    side: str            # always 'long' (spot only)
    entry: float
    stop_loss: float
    take_profit: float
    signal_type: str     # e.g. 'BOS+OB' | 'CHoCH+FVG'
    reason: str


# ── Swing points ──────────────────────────────────────────────────────────────

def detect_swings(df: pd.DataFrame, window: int = 5) -> list[SwingPoint]:
    """Find swing highs and lows using a rolling window."""
    swings: list[SwingPoint] = []
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    for i in range(window, n - window):
        if highs[i] == max(highs[i - window: i + window + 1]):
            swings.append(SwingPoint(index=i, price=highs[i], kind="high"))
        if lows[i] == min(lows[i - window: i + window + 1]):
            swings.append(SwingPoint(index=i, price=lows[i], kind="low"))

    swings.sort(key=lambda s: s.index)
    return swings


# ── BOS / CHoCH ───────────────────────────────────────────────────────────────

def detect_structure_events(
    df: pd.DataFrame, swings: list[SwingPoint]
) -> list[StructureEvent]:
    """
    BOS  (continuation): price breaks beyond the LAST swing in the same direction.
    CHoCH (reversal):    price breaks beyond the LAST swing in the OPPOSITE direction.

    Only BULLISH events are tracked (BOS_bull, CHoCH_bull) since this is a
    spot-only bot that only takes long trades.
    """
    events: list[StructureEvent] = []
    closes = df["close"].values
    n = len(closes)

    highs = [s for s in swings if s.kind == "high"]
    lows  = [s for s in swings if s.kind == "low"]

    if len(swings) < 4:
        return events

    # Build pointer arrays for O(n) lookup instead of O(n²) list comprehensions
    # hi_ptr[i] = index of last swing high with .index < i
    hi_ptr: list[Optional[int]] = [None] * n
    lo_ptr: list[Optional[int]] = [None] * n
    hi_idx = 0
    lo_idx = 0
    last_hi: Optional[SwingPoint] = None
    last_lo: Optional[SwingPoint] = None

    for i in range(n):
        while hi_idx < len(highs) and highs[hi_idx].index < i:
            last_hi = highs[hi_idx]
            hi_idx += 1
        while lo_idx < len(lows) and lows[lo_idx].index < i:
            last_lo = lows[lo_idx]
            lo_idx += 1
        hi_ptr[i] = last_hi
        lo_ptr[i] = last_lo

    start = swings[3].index + 1
    for i in range(start, n):
        c = closes[i]
        prev_high: Optional[SwingPoint] = hi_ptr[i]
        prev_low:  Optional[SwingPoint] = lo_ptr[i]
        if prev_high is None or prev_low is None:
            continue

        # Get last 2 swing highs and lows before i for trend detection
        last2_highs = [s for s in highs if s.index < i][-2:]
        last2_lows  = [s for s in lows  if s.index < i][-2:]

        bullish_trend = (
            len(last2_highs) == 2 and last2_highs[1].price > last2_highs[0].price and
            len(last2_lows)  == 2 and last2_lows[1].price  > last2_lows[0].price
        )
        bearish_trend = (
            len(last2_highs) == 2 and last2_highs[1].price < last2_highs[0].price and
            len(last2_lows)  == 2 and last2_lows[1].price  < last2_lows[0].price
        )

        # BOS bullish — continuation of uptrend
        if bullish_trend and c > prev_high.price:
            if not events or events[-1].bar_index != i:
                events.append(StructureEvent("BOS_bull", prev_high.price, i))

        # CHoCH bullish — reversal: was bearish, now breaks above last swing high
        elif bearish_trend and c > prev_high.price:
            if not events or events[-1].bar_index != i:
                events.append(StructureEvent("CHoCH_bull", prev_high.price, i))

        # NOTE: BOS_bear and CHoCH_bear intentionally omitted —
        # spot-only bot never opens short trades.

    return events


# ── Order Blocks ──────────────────────────────────────────────────────────────

def detect_order_blocks(
    df: pd.DataFrame, events: list[StructureEvent]
) -> list[OrderBlock]:
    """
    Bullish OB: last BEARISH candle before a bullish BOS/CHoCH.
    """
    obs: list[OrderBlock] = []
    opens  = df["open"].values
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values

    for ev in events:
        idx = ev.bar_index
        if idx < 1:
            continue

        if ev.kind in ("BOS_bull", "CHoCH_bull"):
            for j in range(idx - 1, max(0, idx - 20), -1):
                if closes[j] < opens[j]:   # bearish candle
                    obs.append(OrderBlock(
                        kind="bull",
                        top=highs[j],
                        bottom=lows[j],
                        bar_index=j,
                    ))
                    break

    return obs


# ── Fair Value Gaps ───────────────────────────────────────────────────────────

def detect_fvg(df: pd.DataFrame) -> list[FVG]:
    """
    Bullish FVG: candle[i-1].high < candle[i+1].low  (gap up imbalance)
    """
    fvgs: list[FVG] = []
    highs = df["high"].values
    lows  = df["low"].values
    n = len(df)

    for i in range(1, n - 1):
        # Bullish FVG only — spot long trades
        if highs[i - 1] < lows[i + 1]:
            fvgs.append(FVG(
                kind="bull",
                top=lows[i + 1],
                bottom=highs[i - 1],
                bar_index=i,
            ))

    return fvgs


# ── ATR ───────────────────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Average True Range — used for SL buffer.
    BUG FIX: returns NaN when period > len(df). Now falls back to a simple
    high-low range average when the rolling mean is NaN.
    """
    high = df["high"]
    low  = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs(),
    ], axis=1).max(axis=1)

    atr_val = float(tr.rolling(period).mean().iloc[-1])

    if math.isnan(atr_val):
        # Fallback: mean of all available true range values
        atr_val = float(tr.mean())
        logger.debug("ATR rolling mean was NaN — using full-window mean %.6f", atr_val)

    if math.isnan(atr_val) or atr_val <= 0:
        # Last resort: 0.1% of current close
        atr_val = float(df["close"].iloc[-1]) * 0.001
        logger.debug("ATR fallback to 0.1%% of close: %.6f", atr_val)

    return atr_val


# ── Signal generator ──────────────────────────────────────────────────────────

def generate_signal(df: pd.DataFrame, rr: float = 2.0) -> Optional[TradeSignal]:
    """
    Combine BOS / CHoCH → OB / FVG to produce a LONG trade signal.

    Logic:
      1. Detect latest BULLISH structure event (BOS_bull or CHoCH_bull).
      2. Find the nearest bullish OB or FVG.
      3. Entry = midpoint of zone.
      4. SL    = 1 ATR below zone bottom.
      5. TP    = entry + (SL distance × RR).

    Returns None (no signal) or TradeSignal(side='long').
    SHORT signals are never produced — this is a spot-only bot.
    """
    if len(df) < 50:
        return None

    try:
        swings  = detect_swings(df, window=5)
        events  = detect_structure_events(df, swings)
        obs     = detect_order_blocks(df, events)
        fvgs    = detect_fvg(df)
        atr_val = _atr(df)
        current_price = float(df["close"].iloc[-1])
    except Exception as exc:
        logger.warning("SMC analysis error: %s", exc)
        return None

    # Only bullish events remain (bearish filtered in detect_structure_events)
    if not events:
        return None

    last_event = events[-1]

    # ── Bullish OB first ───────────────────────────────────────────────────────
    aligned_obs = [
        ob for ob in obs
        if ob.kind == "bull" and ob.bar_index > len(df) - 50
    ]

    zone_top = zone_bottom = None
    signal_components: list[str] = [last_event.kind.replace("_", " ")]

    if aligned_obs:
        ob = aligned_obs[-1]
        zone_top    = ob.top
        zone_bottom = ob.bottom
        signal_components.append("OB")
    else:
        aligned_fvgs = [
            fvg for fvg in fvgs
            if fvg.kind == "bull" and fvg.bar_index > len(df) - 50
        ]
        if aligned_fvgs:
            fvg = aligned_fvgs[-1]
            zone_top    = fvg.top
            zone_bottom = fvg.bottom
            signal_components.append("FVG")

    if zone_top is None or zone_bottom is None:
        return None

    entry = (zone_top + zone_bottom) / 2
    sl    = zone_bottom - atr_val
    tp    = entry + (entry - sl) * rr

    # Validate: SL must be below entry, TP above entry
    if sl >= entry or tp <= entry:
        logger.debug("Invalid SL/TP geometry — skipping signal")
        return None

    # Stale zone check: price already well above zone
    if current_price > zone_top:
        logger.debug("Price %.6f already above zone %.6f — stale, skipping", current_price, zone_top)
        return None

    # Price must be close to the zone (within 1 ATR) to avoid "random" alerts
    if current_price > zone_top + atr_val:
        logger.debug("Price %.6f too far from zone %.6f — skipping", current_price, zone_top)
        return None

    return TradeSignal(
        side="long",
        entry=round(entry, 6),
        stop_loss=round(sl, 6),
        take_profit=round(tp, 6),
        signal_type="+".join(signal_components),
        reason=(
            f"{last_event.kind} @ {last_event.price:.4f} | "
            f"Zone [{zone_bottom:.4f} – {zone_top:.4f}] | "
            f"ATR {atr_val:.4f}"
        ),
    )
