"""
طبقة التواصل مع Bitget عبر مكتبة ccxt.
"""
import logging
import ccxt

from .config import Config
from .state import shared_state

logger = logging.getLogger("exchange")


class BitgetExchange:
    def __init__(self):
        params = {
            "apiKey": Config.BITGET_API_KEY,
            "secret": Config.BITGET_API_SECRET,
            "password": Config.BITGET_API_PASSPHRASE,
            "enableRateLimit": True,
            "options": {"defaultType": Config.MARKET_TYPE},
        }
        self.client = ccxt.bitget(params)

    @property
    def dry_run(self) -> bool:
        """يقرأ من shared_state عشان يتغير لايف عبر أوامر تليجرام (/dryrun_on, /dryrun_off)."""
        return shared_state.is_dry_run()

    def load_markets(self):
        return self.client.load_markets()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200):
        return self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_balance_usdt(self) -> float:
        try:
            bal = self.client.fetch_balance()
            return float(bal.get("USDT", {}).get("free", 0) or 0)
        except Exception as e:
            logger.error(f"فشل جلب الرصيد: {e}")
            return 0.0

    def set_leverage(self, symbol: str, leverage: int):
        if self.dry_run:
            logger.info(f"[DRY_RUN] set_leverage {symbol} -> {leverage}x")
            return
        try:
            self.client.set_leverage(leverage, symbol)
        except Exception as e:
            logger.warning(f"تعذر ضبط الرافعة على {symbol}: {e}")

    def create_market_order(self, symbol: str, side: str, amount: float, params: dict | None = None):
        """side: 'buy' or 'sell'"""
        params = params or {}
        if self.dry_run:
            logger.info(f"[DRY_RUN] MARKET {side.upper()} {amount} {symbol} params={params}")
            return {"id": "dry-run", "symbol": symbol, "side": side, "amount": amount, "status": "dry_run"}
        return self.client.create_order(symbol, type="market", side=side, amount=amount, params=params)

    def create_stop_order(self, symbol: str, side: str, amount: float, trigger_price: float, order_type: str = "stop"):
        """أمر ستوب لوس / تيك بروفيت"""
        params = {"triggerPrice": trigger_price, "reduceOnly": True}
        if self.dry_run:
            logger.info(f"[DRY_RUN] {order_type.upper()} {side.upper()} {amount} {symbol} @trigger={trigger_price}")
            return {"id": f"dry-run-{order_type}", "status": "dry_run"}
        return self.client.create_order(symbol, type=order_type, side=side, amount=amount, params=params)

    def fetch_open_positions(self, symbols: list[str] | None = None):
        if self.dry_run:
            return []
        try:
            return self.client.fetch_positions(symbols)
        except Exception as e:
            logger.error(f"فشل جلب المراكز المفتوحة: {e}")
            return []

    def close_position_market(self, symbol: str, side: str, amount: float):
        opposite = "sell" if side == "buy" else "buy"
        return self.create_market_order(symbol, opposite, amount, params={"reduceOnly": True})
