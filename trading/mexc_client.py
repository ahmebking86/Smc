"""
MEXC Spot API v3 client — portfolio rebalancing bot.
Compatible with the same interface as BitGetClient.
"""

from __future__ import annotations
import hashlib
import hmac
import time
import logging
import math
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from database.db import get_setting

logger = logging.getLogger(__name__)

_MIN_SELL_USDT = 1.0
MEXC_BASE_URL = "https://api.mexc.com"

_cred_cache: tuple[str, str] | None = None
_cred_cache_ts: float = 0.0
_CRED_TTL = 30.0


def _get_credentials() -> tuple[str, str]:
    global _cred_cache, _cred_cache_ts
    now = time.monotonic()
    if _cred_cache is not None and now - _cred_cache_ts < _CRED_TTL:
        return _cred_cache
    api_key = get_setting("mexc_api_key") or ""
    api_secret = get_setting("mexc_api_secret") or ""
    _cred_cache = (api_key, api_secret)
    _cred_cache_ts = now
    return _cred_cache


def invalidate_mexc_credentials_cache() -> None:
    global _cred_cache, _cred_cache_ts
    _cred_cache = None
    _cred_cache_ts = 0.0


def _sign(query_string: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class MexcClient:
    def __init__(self):
        self.base = MEXC_BASE_URL
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=3,
        )
        self.session.mount("https://", adapter)
        self._precision_cache: dict[str, tuple[int, int]] = {}
        self._min_notional_cache: dict[str, float] = {}
        self._min_base_cache: dict[str, float] = {}

    @property
    def exchange_name(self) -> str:
        return "MEXC"

    def _headers(self) -> dict:
        api_key, _ = _get_credentials()
        return {
            "X-MEXC-APIKEY": api_key,
            "Content-Type": "application/json",
        }

    def _signed_params(self, params: dict | None = None) -> dict:
        api_key, api_secret = _get_credentials()
        if not api_key or not api_secret:
            raise RuntimeError(
                "مفاتيح MEXC API غير مُعيَّنة.\n"
                "اضغط 🔑 إعداد API واختر MEXC."
            )
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = 10000
        query = urlencode(p)
        p["signature"] = _sign(query, api_secret)
        return p

    def _get(self, path: str, params: dict | None = None, signed: bool = False) -> Any:
        if signed:
            params = self._signed_params(params)
        resp = self.session.get(
            self.base + path,
            params=params,
            headers=self._headers() if signed else {},
            timeout=15,
        )
        if not resp.ok:
            body = resp.text[:400]
            logger.error("MEXC GET %s → %s: %s", path, resp.status_code, body)
            raise RuntimeError(f"HTTP {resp.status_code}: {body}")
        data = resp.json()
        # MEXC error format
        if isinstance(data, dict) and data.get("code") not in (None, 200, 0, "200", "0"):
            code = data.get("code")
            msg = data.get("msg") or data.get("message") or str(data)
            raise RuntimeError(f"[{code}] {msg}")
        return data

    def _post(self, path: str, params: dict) -> Any:
        params = self._signed_params(params)
        resp = self.session.post(
            self.base + path,
            params=params,
            headers=self._headers(),
            timeout=15,
        )
        if not resp.ok:
            body = resp.text[:400]
            logger.error("MEXC POST %s → %s: %s", path, resp.status_code, body)
            raise RuntimeError(f"HTTP {resp.status_code}: {body}")
        data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (None, 200, 0, "200", "0"):
            code = data.get("code")
            msg = data.get("msg") or data.get("message") or str(data)
            raise RuntimeError(f"[{code}] {msg}")
        return data

    def has_credentials(self) -> bool:
        api_key, api_secret = _get_credentials()
        return bool(api_key and api_secret)

    def validate_credentials(self) -> tuple[bool, str]:
        try:
            self.get_account_balance()
            return True, ""
        except RuntimeError as e:
            err = str(e)
            if "10072" in err or "api key" in err.lower():
                hint = "API Key غير صحيح أو محذوف."
            elif "10073" in err or "signature" in err.lower():
                hint = "التوقيع خاطئ — تحقق من API Secret."
            elif "700007" in err or "ip" in err.lower():
                hint = "عنوان IP غير مسموح — أزل قيود IP من MEXC."
            else:
                hint = err
            return False, hint
        except Exception as e:
            return False, f"خطأ اتصال: {e}"

    def get_symbol_precision(self, symbol: str) -> tuple[int, int]:
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]
        try:
            data = self._get("/api/v3/exchangeInfo", {"symbol": symbol})
            symbols = data.get("symbols") or []
            if symbols:
                info = symbols[0]
                # baseSizePrecision / quotePrecision / etc.
                qp = int(info.get("baseAssetPrecision") or info.get("baseSizePrecision") or 4)
                pp = int(info.get("quotePrecision") or info.get("quoteAssetPrecision") or 2)
                # filters
                for f in info.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        step = f.get("stepSize", "0.0001")
                        if "." in step:
                            qp = len(step.rstrip("0").split(".")[-1])
                        min_qty = float(f.get("minQty") or 0)
                        if min_qty > 0:
                            self._min_base_cache[symbol] = min_qty
                    if f.get("filterType") == "MIN_NOTIONAL":
                        mn = float(f.get("minNotional") or f.get("notional") or 1)
                        self._min_notional_cache[symbol] = mn
                self._precision_cache[symbol] = (pp, qp)
                return pp, qp
        except Exception as e:
            logger.warning("MEXC get_symbol_precision(%s): %s", symbol, e)
        self._precision_cache[symbol] = (2, 4)
        return 2, 4

    def get_min_notional(self, symbol: str) -> float:
        if symbol not in self._min_notional_cache:
            self.get_symbol_precision(symbol)
        return self._min_notional_cache.get(symbol, 1.0)

    def get_min_base_qty(self, symbol: str) -> float:
        if symbol not in self._min_base_cache:
            self.get_symbol_precision(symbol)
        return self._min_base_cache.get(symbol, 0.0)

    def get_ticker(self, symbol: str) -> dict:
        data = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        if isinstance(data, list):
            data = data[0] if data else {}
        if not data or "lastPrice" not in data:
            raise RuntimeError(f"الزوج {symbol} غير موجود على MEXC")
        return data

    def get_price(self, symbol: str) -> float:
        t = self.get_ticker(symbol)
        return float(t.get("lastPrice") or t.get("price") or 0)

    def get_all_tickers(self) -> dict[str, float]:
        data = self._get("/api/v3/ticker/24hr")
        prices: dict[str, float] = {}
        items = data if isinstance(data, list) else []
        for t in items:
            sym = t.get("symbol", "")
            if sym.endswith("USDT"):
                coin = sym[:-4]
                try:
                    prices[coin] = float(t.get("lastPrice") or 0)
                except (TypeError, ValueError):
                    pass
        return prices

    def get_account_balance(self) -> list[dict]:
        data = self._get("/api/v3/account", signed=True)
        balances = data.get("balances") or []
        # Normalize to Bitget-like format
        result = []
        for b in balances:
            free = float(b.get("free") or 0)
            locked = float(b.get("locked") or 0)
            if free + locked <= 0:
                continue
            result.append({
                "coin": b.get("asset", "").upper(),
                "available": free,
                "availableBalance": free,
                "free": free,
                "locked": locked,
            })
        return result

    def place_market_sell(self, symbol: str, qty: float, qty_places: int = 6) -> dict:
        size = f"{qty:.{qty_places}f}".rstrip("0").rstrip(".")
        return self._post("/api/v3/order", {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": size,
        })

    def place_market_buy_usdt(self, symbol: str, usdt_amount: float) -> dict:
        # MEXC market buy uses quoteOrderQty
        return self._post("/api/v3/order", {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": f"{usdt_amount:.2f}",
        })

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._post("/api/v3/order", {
            "symbol": symbol,
            "orderId": order_id,
            # Actually DELETE, but some endpoints accept POST cancel
        })

    def get_open_orders(self, symbol: str) -> list[dict]:
        data = self._get("/api/v3/openOrders", {"symbol": symbol}, signed=True)
        return data if isinstance(data, list) else []

    def cancel_symbol_orders_batch(self, symbol: str) -> bool:
        try:
            self._delete("/api/v3/openOrders", {"symbol": symbol})
        except Exception as e:
            logger.warning("MEXC batch cancel %s: %s", symbol, e)
        return True

    def _delete(self, path: str, params: dict) -> Any:
        params = self._signed_params(params)
        resp = self.session.delete(
            self.base + path,
            params=params,
            headers=self._headers(),
            timeout=15,
        )
        if not resp.ok:
            body = resp.text[:400]
            logger.error("MEXC DELETE %s → %s: %s", path, resp.status_code, body)
            raise RuntimeError(f"HTTP {resp.status_code}: {body}")
        return resp.json()

    def close_all_at_market(self, symbols: list[str]) -> dict:
        cancelled_orders = 0
        market_sells: list[str] = []
        errors: list[str] = []

        for sym in set(symbols):
            try:
                self.cancel_symbol_orders_batch(sym)
                cancelled_orders += 1
            except Exception as e:
                errors.append(f"cancel {sym}: {e}")

        if cancelled_orders:
            time.sleep(2)

        balances: dict[str, float] = {}
        try:
            for item in self.get_account_balance():
                coin = (item.get("coin") or "").upper()
                avail = float(item.get("available") or 0)
                if coin and avail > 0:
                    balances[coin] = avail
        except Exception as e:
            errors.append(f"balance: {e}")

        for sym in set(symbols):
            coin = sym[:-4] if sym.endswith("USDT") else sym
            qty = balances.get(coin, 0)
            if qty <= 0:
                continue
            try:
                price = self.get_price(sym)
                if qty * price < _MIN_SELL_USDT:
                    continue
                _, qp = self.get_symbol_precision(sym)
                min_base = self.get_min_base_qty(sym)
                if min_base > 0 and qty < min_base:
                    continue
                self.place_market_sell(sym, qty, qp)
                market_sells.append(f"{coin}: {qty} ≈ {qty * price:.2f} USDT")
                time.sleep(0.4)
            except Exception as e:
                errors.append(f"sell {coin}: {e}")

        return {
            "cancelled_orders": cancelled_orders,
            "market_sells": market_sells,
            "errors": errors,
        }


_mexc_client: MexcClient | None = None


def get_mexc() -> MexcClient:
    global _mexc_client
    if _mexc_client is None:
        _mexc_client = MexcClient()
    return _mexc_client
