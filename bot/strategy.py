"""
استراتيجية سكالبينج: EMA سريع/بطيء + فلتر RSI + ستوب/تارجت ديناميكي بـ ATR.

فكرة الاستراتيجية:
- دخول شراء: EMA_FAST يقطع EMA_SLOW لأعلى (كروس صاعد) + RSI مش في منطقة تشبع شرائي.
- دخول بيع: EMA_FAST يقطع EMA_SLOW لأسفل (كروس هابط) + RSI مش في منطقة تشبع بيعي.
- ستوب لوس وجني أرباح بيتحسبوا من ATR (تقلب السوق) مش أرقام ثابتة،
  عشان يتناسب مع كل عملة وكل ظرف سوق تلقائياً.
- الأطر الزمنية القصيرة (1m/3m) مناسبة للسكالبينج.
"""
from dataclasses import dataclass
import pandas as pd
import numpy as np

from .config import Config


@dataclass
class Signal:
    action: str  # "buy", "sell", "hold"
    price: float
    atr: float
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = ""


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def ohlcv_to_df(ohlcv: list) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
    return df


def generate_signal(ohlcv: list) -> Signal:
    df = ohlcv_to_df(ohlcv)
    if len(df) < max(Config.EMA_SLOW, Config.RSI_PERIOD, Config.ATR_PERIOD) + 5:
        return Signal(action="hold", price=float(df["close"].iloc[-1]), atr=0.0, reason="بيانات غير كافية")

    df["ema_fast"] = _ema(df["close"], Config.EMA_FAST)
    df["ema_slow"] = _ema(df["close"], Config.EMA_SLOW)
    df["rsi"] = _rsi(df["close"], Config.RSI_PERIOD)
    df["atr"] = _atr(df, Config.ATR_PERIOD)

    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(last["close"])
    atr = float(last["atr"])

    crossed_up = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    crossed_down = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]

    if crossed_up and last["rsi"] < Config.RSI_OVERBOUGHT:
        sl = price - atr * Config.ATR_SL_MULT
        tp = price + atr * Config.ATR_TP_MULT
        return Signal(action="buy", price=price, atr=atr, stop_loss=sl, take_profit=tp,
                       reason=f"EMA cross up, RSI={last['rsi']:.1f}")

    if crossed_down and last["rsi"] > Config.RSI_OVERSOLD:
        sl = price + atr * Config.ATR_SL_MULT
        tp = price - atr * Config.ATR_TP_MULT
        return Signal(action="sell", price=price, atr=atr, stop_loss=sl, take_profit=tp,
                       reason=f"EMA cross down, RSI={last['rsi']:.1f}")

    return Signal(action="hold", price=price, atr=atr, reason="لا يوجد إشارة كروس حالياً")
