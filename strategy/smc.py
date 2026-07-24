"""strategy/smc.py — Smart Money Concepts analysis.

Detects:
  - Swing Highs / Swing Lows
  - BOS  (Break of Structure)
  - CHoCH (Change of Character)
  - OB   (Order Block)
  - FVG  (Fair Value Gap)

Returns a TradeSignal or None.
"""
from __future__ import annotations

import logging
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
    side: str            # 'long' | 'short'
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
        # Swing high: highest in window on both sides
        if highs[i] == max(highs[i - window: i + window + 1]):
            swings.append(SwingPoint(index=i, price=highs[i], kind="high"))
        # Swing low: lowest in window on both sides
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
    """
    events: list[StructureEvent] = []
    closes = df["close"].values
    n = len(closes)

    highs = [s for s in swings if s.kind == "high"]
    lows  = [s for s in swings if s.kind == "low"]

    def last_before(lst: list[SwingPoint], idx: int) -> Optional[SwingPoint]:
        candidates = [s for s in lst if s.index < idx]
        return candidates[-1] if candidates else None

    # Determine current trend from last two swings (simplified)
    if len(swings) < 4:
        return events

    # Walk candles after enough swings exist
    start = swings[3].index + 1
    for i in range(start, n):
        c = closes[i]
        prev_high = last_before(highs, i)
        prev_low  = last_before(lows, i)
        if not prev_high or not prev_low:
            continue

        # Determine trend: higher highs + higher lows = bullish, else bearish
        # Use last two swing highs and lows
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

        # BOS bullish: bullish trend, close breaks above last swing high
        if bullish_trend and c > prev_high.price:
            if not events or events[-1].bar_index != i:
                events.append(StructureEvent("BOS_bull", prev_high.price, i))

        # BOS bearish: bearish trend, close breaks below last swing low
        elif bearish_trend and c < prev_low.price:
            if not events or events[-1].bar_index != i:
                events.append(StructureEvent("BOS_bear", prev_low.price, i))

        # CHoCH bullish: was bearish, but now breaks above last swing high
        elif bearish_trend and c > prev_high.price:
            if not events or events[-1].bar_index != i:
                events.append(StructureEvent("CHoCH_bull", prev_high.price, i))

        # CHoCH bearish: was bullish, but now breaks below last swing low
        elif bullish_trend and c < prev_low.price:
            if not events or events[-1].bar_index != i:
                events.append(StructureEvent("CHoCH_bear", prev_low.price, i))

    return events


# ── Order Blocks ──────────────────────────────────────────────────────────────

def detect_order_blocks(
    df: pd.DataFrame, events: list[StructureEvent]
) -> list[OrderBlock]:
    """
    Bullish OB: last BEARISH candle before a bullish BOS/CHoCH.
    Bearish OB: last BULLISH candle before a bearish BOS/CHoCH.
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
            # Find last bearish candle before the event
            for j in range(idx - 1, max(0, idx - 20), -1):
                if closes[j] < opens[j]:   # bearish candle
                    obs.append(OrderBlock(
                        kind="bull",
                        top=highs[j],
                        bottom=lows[j],
                        bar_index=j,
                    ))
                    break

        elif ev.kind in ("BOS_bear", "CHoCH_bear"):
            # Find last bullish candle before the event
            for j in range(idx - 1, max(0, idx - 20), -1):
                if closes[j] > opens[j]:   # bullish candle
                    obs.append(OrderBlock(
                        kind="bear",
                        top=highs[j],
                        bottom=lows[j],
                        bar_index=j,
                    ))
                    break

    return obs


# ── Fair Value Gaps ───────────────────────────────────────────────────────────

def detect_fvg(df: pd.DataFrame) -> list[FVG]:
    """
    Bullish FVG: candle[i-1].high < candle[i+1].low  (gap up — price left an imbalance)
    Bearish FVG: candle[i-1].low  > candle[i+1].high (gap down)
    """
    fvgs: list[FVG] = []
    highs = df["high"].values
    lows  = df["low"].values
    n = len(df)

    for i in range(1, n - 1):
        # Bullish FVG
        if highs[i - 1] < lows[i + 1]:
            fvgs.append(FVG(
                kind="bull",
                top=lows[i + 1],
                bottom=highs[i - 1],
                bar_index=i,
            ))
        # Bearish FVG
        elif lows[i - 1] > highs[i + 1]:
            fvgs.append(FVG(
                kind="bear",
                top=lows[i - 1],
                bottom=highs[i + 1],
                bar_index=i,
            ))

    return fvgs


# ── Signal generator ──────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range — used for SL buffer."""
    high = df["high"]
    low  = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def generate_signal(df: pd.DataFrame, rr: float = 2.0) -> Optional[TradeSignal]:
    """
    Combine BOS / CHoCH → OB / FVG to produce a trade signal.

    Logic:
      1. Detect latest structure event (BOS or CHoCH).
      2. Find the nearest OB or FVG that aligns with the signal direction.
      3. Entry = midpoint of OB/FVG zone.
      4. SL    = 1 ATR beyond the OB/FVG zone extremity.
      5. TP    = entry ± (SL distance × RR).
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

    if not events:
        return None

    last_event = events[-1]
    direction  = "long" if "bull" in last_event.kind else "short"

    # ── Try Order Block first ──────────────────────────────────────────────────
    aligned_obs = [
        ob for ob in obs
        if ob.kind == ("bull" if direction == "long" else "bear")
        and ob.bar_index > len(df) - 50   # recent OBs only
    ]

    zone_top = zone_bottom = None
    signal_components: list[str] = [last_event.kind.replace("_", " ")]

    if aligned_obs:
        ob = aligned_obs[-1]
        zone_top    = ob.top
        zone_bottom = ob.bottom
        signal_components.append("OB")
    else:
        # Fall back to FVG
        aligned_fvgs = [
            fvg for fvg in fvgs
            if fvg.kind == ("bull" if direction == "long" else "bear")
            and fvg.bar_index > len(df) - 50
        ]
        if aligned_fvgs:
            fvg = aligned_fvgs[-1]
            zone_top    = fvg.top
            zone_bottom = fvg.bottom
            signal_components.append("FVG")

    if zone_top is None or zone_bottom is None:
        return None

    entry = (zone_top + zone_bottom) / 2

    if direction == "long":
        sl = zone_bottom - atr_val
        tp = entry + (entry - sl) * rr
        # Only signal if price is near or inside the zone (entry is valid)
        if current_price > zone_top * 1.01:   # price already above zone, stale
            return None
    else:
        sl = zone_top + atr_val
        tp = entry - (sl - entry) * rr
        if current_price < zone_bottom * 0.99:
            return None

    return TradeSignal(
        side=direction,
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
