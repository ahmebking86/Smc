"""
MEXC Spot REST API client — for portfolio rebalancing bot.
No passphrase required (unlike BitGet).
Fixed: signature encoding (700002).
"""

from __future__ import annotations
import hashlib
import hmac
import time
import logging
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from database.db import get_setting

logger = logging.getLogger(__name__)

MEXC_BASE_URL = "https://api.mexc.com"
_MIN_SELL_USDT = 1.0

_cred_cache: tuple[str, str] | None = None
_cred_cache_ts: float = 0.0
_CRED_TTL = 30.0


def _get_credentials() -> tuple[str, str]:
    global _cred_cache, _cred_cache_ts
    now = time.monotonic()
    if _cred_cache is not None and now - _cred_cache_ts < _CRED_TTL:
        return _cred_cache
    api_key = (get_setting("mexc_api_key") or "").strip()
    api_secret = (get_setting("mexc_api_secret") or "").strip()
    _cred_cache = (api_key, api_secret)
    _cred_cache_ts = now
    return _cred_cache


def invalidate_mexc_credentials_cache() -> None:
    global _cred_cache, _cred_cache_ts
    _cred_cache = None
    _cred_cache_ts = 0.0


def _sign(query: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class MexcClient:
    exchange_name = "MEXC"

    def __init__(self):
        self.base = MEXC_BASE_URL
        self.session = requests.Session()
        self._precision_cache: dict[str, tuple[int, int]] = {}
        self._min_notional_cache: dict[str, float] = {}
        self._min_base_cache: dict[str, float] = {}

    def _headers(self) -> dict:
        api_key, _ = _get_credentials()
        return {
            "X-MEXC-APIKEY": api_key,
            "Content-Type": "application/json",
        }

    def _build_signed_query(self, params: dict | None = None) -> str:
        """Build query string + signature exactly as MEXC expects.

        Critical: sign the *exact* string that will be sent (no double-encoding).
        """
        api_key, api_secret = _get_credentials()
        if not api_key or not api_secret:
            raise RuntimeError(
                "مفاتيح MEXC API غير مُعيَّنة.\n"
                "اضغط ⚙️ إعدادات API من القائمة الرئيسية."
            )
        p = {k: str(v) for k, v in (params or {}).items()}
        p["timestamp"] = str(int(time.time() * 1000))
        p["recvWindow"] = "5000"
        # alphabetical order, no encoding surprises
        query = urlencode(sorted(p.items()))
        signature = _sign(query, api_secret)
        return query + "&signature=" + signature

    def _get(self, path: str, params: dict | None = None, signed: bool = False) -> Any:
        headers = self._headers() if signed else {}
        if signed:
            qs = self._build_signed_query(params)
            url = f"{self.base}{path}?{qs}"
            resp = self.session.get(url, headers=headers, timeout=15)
        else:
            resp = self.session.get(
                self.base + path,
                params=params,
                headers={},
                timeout=15,
            )
        if not resp.ok:
            body = resp.text[:400]
            logger.error("MEXC GET %s → %s: %s", path, resp.status_code, body)
            raise RuntimeError(f"HTTP {resp.status_code}: {body}")
        data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0, 200):
            raise RuntimeError(f"[{data.get('code')}] {data.get('msg', 'MEXC API error')}")
        return data

    def _post(self, path: str, params: dict) -> Any:
        """POST with signed form body (MEXC official style)."""
        qs = self._build_signed_query(params)
        # Send as application/x-www-form-urlencoded body
        headers = self._headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self.session.post(
            self.base + path,
            data=qs,
            headers=headers,
            timeout=15,
        )
        if not resp.ok:
            body = resp.text[:400]
            logger.error("MEXC POST %s → %s: %s", path, resp.status_code, body)
            raise RuntimeError(f"HTTP {resp.status_code}: {body}")
        data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0, 200):
            raise RuntimeError(f"[{data.get('code')}] {data.get('msg', 'MEXC API error')}")
        return data

    def _delete(self, path: str, params: dict) -> Any:
        qs = self._build_signed_query(params)
        url = f"{self.base}{path}?{qs}"
        resp = self.session.delete(url, headers=self._headers(), timeout=15)
        if not resp.ok:
            body = resp.text[:400]
            logger.error("MEXC DELETE %s → %s: %s", path, resp.status_code, body)
            raise RuntimeError(f"HTTP {resp.status_code}: {body}")
        data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0, 200):
            raise RuntimeError(f"[{data.get('code')}] {data.get('msg', 'MEXC API error')}")
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
            if "10072" in err or "Invalid API key" in err.lower():
                hint = "API Key غير صحيح أو تم حذفه."
            elif "700002" in err or "signature" in err.lower():
                hint = "التوقيع خاطئ — تحقق من API Secret (انسخه تاني بدون مسافات)."
            elif "700003" in err or "timestamp" in err.lower():
                hint = "فرق توقيت السيرفر كبير — تأكد من ساعة السيرفر."
            elif "ip" in err.lower() or "whitelist" in err.lower() or "700006" in err:
                hint = "عنوان IP غير مسموح — أزل قيود IP أو أضف IP السيرفر."
            elif "permission" in err.lower() or "700007" in err:
                hint = "صلاحيات غير كافية — فعّل Spot Trading."
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
            symbols = data.get("symbols", []) if isinstance(data, dict) else []
            if symbols:
                info = symbols[0]
                pp, qp = 2, 4
                for f in info.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        tick = str(f.get("tickSize", "0.01"))
                        if "." in tick:
                            pp = len(tick.rstrip("0").split(".")[-1])
                        else:
                            pp = 0
                    if f.get("filterType") == "LOT_SIZE":
                        step = str(f.get("stepSize", "0.0001"))
                        if "." in step:
                            qp = len(step.rstrip("0").split(".")[-1])
                        else:
                            qp = 0
                        try:
                            self._min_base_cache[symbol] = float(f.get("minQty") or 0)
                        except (TypeError, ValueError):
                            pass
                    if f.get("filterType") == "MIN_NOTIONAL":
                        try:
                            self._min_notional_cache[symbol] = float(
                                f.get("minNotional") or f.get("notional") or 1.0
                            )
                        except (TypeError, ValueError):
                            pass
                # fallback: baseAssetPrecision / quoteAssetPrecision
                if "baseAssetPrecision" in info:
                    qp = int(info.get("baseAssetPrecision") or qp)
                if "quoteAssetPrecision" in info:
                    pp = int(info.get("quoteAssetPrecision") or pp)
                self._precision_cache[symbol] = (pp, qp)
                return pp, qp
        except Exception as e:
            logger.warning("get_symbol_precision %s: %s", symbol, e)
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

    def get_price(self, symbol: str) -> float:
        data = self._get("/api/v3/ticker/price", {"symbol": symbol})
        if isinstance(data, dict) and "price" in data:
            return float(data["price"])
        raise RuntimeError(f"الزوج {symbol} غير موجود على MEXC")

    def get_ticker(self, symbol: str) -> dict:
        data = self._get("/api/v3/ticker/24hr", {"symbol": symbol})
        if isinstance(data, dict):
            return data
        raise RuntimeError(f"الزوج {symbol} غير موجود على MEXC")

    def get_account_balance(self) -> list[dict]:
        data = self._get("/api/v3/account", signed=True)
        balances = data.get("balances", []) if isinstance(data, dict) else []
        result = []
        for b in balances:
            free = float(b.get("free") or 0)
            locked = float(b.get("locked") or 0)
            if free + locked <= 0:
                continue
            result.append({
                "coin": (b.get("asset") or "").upper(),
                "available": free,
                "availableBalance": free,
                "free": free,
                "frozen": locked,
                "locked": locked,
            })
        return result

    def place_market_buy_usdt(self, symbol: str, usdt_amount: float) -> dict:
        return self._post("/api/v3/order", {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": f"{usdt_amount:.2f}",
        })

    def place_market_sell(self, symbol: str, qty: float, qty_places: int = 6) -> dict:
        return self._post("/api/v3/order", {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": f"{qty:.{qty_places}f}",
        })

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._delete("/api/v3/order", {
            "symbol": symbol,
            "orderId": order_id,
        })

    def liquidate_wallet(self) -> dict:
        """Sell all non-USDT balances to USDT (best effort)."""
        result = {"cancelled_orders": 0, "sold": [], "errors": [], "skipped": []}
        try:
            balances = self.get_account_balance()
        except Exception as e:
            result["errors"].append(str(e))
            return result
        for b in balances:
            coin = b.get("coin", "")
            if coin in ("USDT", "USDC", ""):
                continue
            qty = float(b.get("available") or 0)
            if qty <= 0:
                continue
            symbol = coin + "USDT"
            try:
                price = self.get_price(symbol)
                if qty * price < _MIN_SELL_USDT:
                    result["skipped"].append(f"{coin}: قيمة صغيرة")
                    continue
                _, qp = self.get_symbol_precision(symbol)
                sell_qty = round(qty, qp)
                if sell_qty <= 0:
                    continue
                self.place_market_sell(symbol, sell_qty, qp)
                result["sold"].append(f"{coin}: {sell_qty}")
                time.sleep(0.3)
            except Exception as e:
                result["errors"].append(f"{coin}: {e}")
        return result


_client: Optional[MexcClient] = None


def get_mexc() -> MexcClient:
    global _client
    if _client is None:
        _client = MexcClient()
    return _client
