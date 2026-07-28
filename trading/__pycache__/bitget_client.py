"""
BitGet REST API v2 client — spot trading.
Adapted for portfolio rebalancing bot.
"""

from __future__ import annotations
import hashlib
import hmac
import base64
import json
import math
import time
import logging
from typing import Any, Optional

import requests

from config import BITGET_BASE_URL
from database.db import get_setting

logger = logging.getLogger(__name__)

_MIN_SELL_USDT = 1.0

_cred_cache: tuple[str, str, str] | None = None
_cred_cache_ts: float = 0.0
_CRED_TTL = 30.0


def _get_credentials() -> tuple[str, str, str]:
    global _cred_cache, _cred_cache_ts
    now = time.monotonic()
    if _cred_cache is not None and now - _cred_cache_ts < _CRED_TTL:
        return _cred_cache
    api_key    = get_setting("bitget_api_key")    or ""
    api_secret = get_setting("bitget_api_secret") or ""
    passphrase = get_setting("bitget_passphrase") or ""
    _cred_cache    = (api_key, api_secret, passphrase)
    _cred_cache_ts = now
    return _cred_cache


def invalidate_credentials_cache() -> None:
    global _cred_cache, _cred_cache_ts
    _cred_cache = None
    _cred_cache_ts = 0.0


def _sign(message: str, secret: str) -> str:
    mac = hmac.new(secret.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _ts() -> str:
    return str(int(time.time() * 1000))


def _auth_headers(method: str, path: str, body: str = "") -> dict:
    api_key, api_secret, passphrase = _get_credentials()
    if not api_key or not api_secret or not passphrase:
        raise RuntimeError(
            "مفاتيح BitGet API غير مُعيَّنة.\n"
            "اضغط 🔑 إعداد API من القائمة الرئيسية."
        )
    ts  = _ts()
    pre = ts + method.upper() + path + body
    return {
        "ACCESS-KEY":        api_key,
        "ACCESS-SIGN":       _sign(pre, api_secret),
        "ACCESS-TIMESTAMP":  ts,
        "ACCESS-PASSPHRASE": passphrase,
        "locale":            "en-US",
    }


class BitGetClient:
    def __init__(self):
        self.base = BITGET_BASE_URL
        self.session = requests.Session()
        self._precision_cache: dict[str, tuple[int, int]] = {}
        self._min_notional_cache: dict[str, float] = {}
        self._min_base_cache: dict[str, float] = {}

    def _get(self, path: str, params: dict | None = None) -> Any:
        qs = ""
        if params:
            qs = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        full_path = path + qs
        headers = _auth_headers("GET", full_path)
        resp = self.session.get(self.base + full_path, headers=headers, timeout=15)
        if not resp.ok:
            body = resp.text[:400]
            logger.error("BitGet GET %s → %s: %s", full_path, resp.status_code, body)
            raise RuntimeError(f"HTTP {resp.status_code}: {body}")
        data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"[{data.get('code')}] {data.get('msg', 'BitGet API error')}")
        return data.get("data", {})

    def _post(self, path: str, body: dict) -> Any:
        body_str = json.dumps(body)
        headers = _auth_headers("POST", path, body_str)
        headers["Content-Type"] = "application/json"
        resp = self.session.post(self.base + path, headers=headers, data=body_str, timeout=15)
        if not resp.ok:
            body_txt = resp.text[:400]
            logger.error("BitGet POST %s → %s: %s", path, resp.status_code, body_txt)
            raise RuntimeError(f"HTTP {resp.status_code}: {body_txt}")
        data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"[{data.get('code')}] {data.get('msg', 'BitGet API error')}")
        return data.get("data", {})

    def get_symbol_precision(self, symbol: str) -> tuple[int, int]:
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]
        try:
            resp = self.session.get(
                self.base + "/api/v2/spot/public/symbols",
                params={"symbol": symbol},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", []) if data.get("code") == "00000" else []
            if items:
                info = items[0]
                pp_raw = (info.get("pricePrecision") or info.get("pricePlace")
                          or info.get("priceScale") or info.get("pricePrec"))
                qp_raw = (info.get("quantityPrecision") or info.get("quantityPlace")
                          or info.get("quantityScale") or info.get("basePrecision"))
                pp = int(pp_raw) if pp_raw is not None else 2
                qp = int(qp_raw) if qp_raw is not None else 2
                if "VIRTUAL" in symbol:
                    pp, qp = 4, 0
                if "PI" in symbol and not symbol.startswith("PIE"):
                    pp, qp = 2, 1
                if "ACN" in symbol:
                    pp, qp = 6, 0
                mn_raw = (info.get("minTradeUSDT") or info.get("minTradeAmount")
                          or info.get("minOrderAmt"))
                if mn_raw is not None:
                    try:
                        self._min_notional_cache[symbol] = float(mn_raw)
                    except (TypeError, ValueError):
                        pass
                mb_raw = (info.get("minTradeNum") or info.get("baseSizeMin")
                          or info.get("minBaseTradeSize") or info.get("minQty")
                          or info.get("minOrderSize") or info.get("baseMinSize"))
                if mb_raw is not None:
                    try:
                        self._min_base_cache[symbol] = float(mb_raw)
                    except (TypeError, ValueError):
                        pass
                self._precision_cache[symbol] = (pp, qp)
                return pp, qp
        except Exception as e:
            logger.warning("get_symbol_precision(%s): %s — defaults", symbol, e)
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

    def has_credentials(self) -> bool:
        api_key, api_secret, passphrase = _get_credentials()
        return bool(api_key and api_secret and passphrase)

    def validate_credentials(self) -> tuple[bool, str]:
        try:
            self.get_account_balance()
            return True, ""
        except RuntimeError as e:
            err = str(e)
            if "40037" in err:
                hint = "API Key غير موجود أو تم حذفه."
            elif "40007" in err:
                hint = "التوقيع خاطئ — تحقق من API Secret."
            elif "40011" in err:
                hint = "Passphrase خاطئ."
            elif "40309" in err or "ip" in err.lower():
                hint = "عنوان IP غير مسموح — أزل قيود IP من BitGet."
            elif "43114" in err or "permission" in err.lower():
                hint = "صلاحيات غير كافية — فعّل Read + Spot Trade."
            else:
                hint = err
            return False, hint
        except Exception as e:
            return False, f"خطأ اتصال: {e}"

    def get_ticker(self, symbol: str) -> dict:
        resp = self.session.get(
            self.base + "/api/v2/spot/market/tickers",
            params={"symbol": symbol},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("data", [])
        if data.get("code") == "00000" and result:
            return result[0]
        raise RuntimeError(f"الزوج {symbol} غير موجود على BitGet")

    def get_price(self, symbol: str) -> float:
        return float(self.get_ticker(symbol)["lastPr"])

    def get_all_tickers(self) -> dict[str, float]:
        resp = self.session.get(self.base + "/api/v2/spot/market/tickers", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        prices: dict[str, float] = {}
        for t in data.get("data", []):
            sym = t.get("symbol", "")
            if sym.endswith("USDT"):
                coin = sym[:-4]
                try:
                    prices[coin] = float(t["lastPr"])
                except (KeyError, ValueError):
                    pass
        return prices

    def get_account_balance(self) -> list[dict]:
        result = self._get("/api/v2/spot/account/assets")
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("assets", result.get("list", []))
        return []

    def place_market_sell(self, symbol: str, qty: float, qty_places: int = 6) -> dict:
        return self._post("/api/v2/spot/trade/place-order", {
            "symbol":    symbol,
            "side":      "sell",
            "orderType": "market",
            "force":     "ioc",
            "size":      f"{qty:.{qty_places}f}",
        })

    def place_market_buy_usdt(self, symbol: str, usdt_amount: float) -> dict:
        return self._post("/api/v2/spot/trade/place-order", {
            "symbol":    symbol,
            "side":      "buy",
            "orderType": "market",
            "force":     "fok",
            "size":      f"{usdt_amount:.2f}",
        })

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._post("/api/v2/spot/trade/cancel-order", {
            "symbol":  symbol,
            "orderId": order_id,
        })

    def get_open_orders(self, symbol: str) -> list[dict]:
        data = self._get("/api/v2/spot/trade/unfilled-orders", {"symbol": symbol})
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("orderList", data.get("list", []))
        return []

    def cancel_symbol_orders_batch(self, symbol: str) -> bool:
        try:
            self._post("/api/v2/spot/trade/cancel-symbol-order", {"symbol": symbol})
        except Exception as e:
            logger.warning("batch cancel %s: %s", symbol, e)
        total = 0
        for _ in range(5):
            try:
                data = self._get("/api/v2/spot/trade/unfilled-orders",
                                 {"symbol": symbol, "limit": "200"})
            except Exception:
                break
            orders = (data if isinstance(data, list)
                      else data.get("orderList", data.get("list", []))
                      if isinstance(data, dict) else [])
            if not orders:
                break
            cancelled = 0
            for o in orders:
                try:
                    self.cancel_order(symbol, o["orderId"])
                    total += 1
                    cancelled += 1
                except Exception:
                    pass
            if cancelled == 0:
                break
        return True

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
            time.sleep(3)
        balances: dict[str, float] = {}
        try:
            for item in self.get_account_balance():
                coin = (item.get("coin") or item.get("currency") or "").upper()
                avail = float(item.get("available") or item.get("availableBalance")
                              or item.get("free") or 0)
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
                market_sells.append(f"{coin}: {qty} ≈ {qty*price:.2f} USDT")
                time.sleep(0.3)
            except Exception as e:
                errors.append(f"sell {coin}: {e}")
        return {
            "cancelled_orders": cancelled_orders,
            "market_sells": market_sells,
            "errors": errors,
        }

    def liquidate_wallet(self) -> dict:
        """Cancel all open orders and sell every non-USDT asset to USDT."""
        cancelled = 0
        sold: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []
        # Cancel open orders for known USDT pairs we hold
        try:
            balances_raw = self.get_account_balance()
        except Exception as e:
            return {"cancelled_orders": 0, "sold": [], "skipped": [], "errors": [str(e)]}

        coins = []
        for item in balances_raw:
            coin = (item.get("coin") or item.get("currency") or "").upper()
            if not coin or coin == "USDT":
                continue
            avail = float(item.get("available") or item.get("availableBalance")
                          or item.get("free") or 0)
            frozen = float(item.get("frozen") or item.get("locked") or 0)
            if avail + frozen > 0:
                coins.append(coin)

        for coin in coins:
            sym = coin + "USDT"
            try:
                self.cancel_symbol_orders_batch(sym)
                cancelled += 1
            except Exception:
                pass
        if cancelled:
            time.sleep(4)

        # Refresh balances
        try:
            balances_raw = self.get_account_balance()
        except Exception as e:
            errors.append(str(e))
            balances_raw = []

        for item in balances_raw:
            coin = (item.get("coin") or item.get("currency") or "").upper()
            if not coin or coin == "USDT":
                continue
            avail = float(item.get("available") or item.get("availableBalance")
                          or item.get("free") or 0)
            if avail <= 0:
                continue
            sym = coin + "USDT"
            try:
                price = self.get_price(sym)
                val = avail * price
                if val < _MIN_SELL_USDT:
                    skipped.append(f"{coin}: {avail} (dust {val:.4f}$)")
                    continue
                _, qp = self.get_symbol_precision(sym)
                min_base = self.get_min_base_qty(sym)
                if min_base > 0 and avail < min_base:
                    skipped.append(f"{coin}: below min base qty")
                    continue
                self.place_market_sell(sym, avail, qp)
                sold.append(f"{coin}: {avail} ≈ {val:.2f} USDT")
                time.sleep(0.35)
            except Exception as e:
                # pair may not exist
                errors.append(f"{coin}: {e}")
        return {
            "cancelled_orders": cancelled,
            "sold": sold,
            "skipped": skipped,
            "errors": errors,
        }


_client: Optional[BitGetClient] = None


def get_bitget() -> BitGetClient:
    global _client
    if _client is None:
        _client = BitGetClient()
    return _client
