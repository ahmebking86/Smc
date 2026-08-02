"""
MEXC Client - Spot only
"""

import ccxt.async_support as ccxt
from loguru import logger
import config.settings as config


class MexcClient:
    def __init__(self):
        self.exchange = ccxt.mexc({
            "apiKey": config.MEXC_API_KEY,
            "secret": config.MEXC_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self.markets_loaded = False

    async def load_markets(self):
        try:
            await self.exchange.load_markets()
            self.markets_loaded = True
            logger.info("MEXC markets loaded")
        except Exception as e:
            logger.error(f"Failed to load markets: {e}")

    async def fetch_ticker(self, symbol: str):
        try:
            return await self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"fetch_ticker {symbol}: {e}")
            return None

    async def fetch_balance(self):
        try:
            return await self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"fetch_balance: {e}")
            return {}

    async def create_market_buy(self, symbol: str, amount: float):
        if config.MODE != "real":
            logger.info(f"[PAPER] Market BUY {symbol} amount={amount}")
            return {"id": "paper", "symbol": symbol, "amount": amount}
        try:
            order = await self.exchange.create_market_buy_order(symbol, amount)
            logger.success(f"BUY order placed: {symbol}")
            return order
        except Exception as e:
            logger.error(f"create_market_buy error: {e}")
            return None

    async def create_market_sell(self, symbol: str, amount: float):
        if config.MODE != "real":
            logger.info(f"[PAPER] Market SELL {symbol} amount={amount}")
            return {"id": "paper", "symbol": symbol, "amount": amount}
        try:
            order = await self.exchange.create_market_sell_order(symbol, amount)
            logger.success(f"SELL order placed: {symbol}")
            return order
        except Exception as e:
            logger.error(f"create_market_sell error: {e}")
            return None

    async def close(self):
        await self.exchange.close()


mexc = MexcClient()
