"""
BitGet REST API v2 client — spot trading.
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

# Hard minimum sell value — any holding worth less than this in USDT is
# treated as dust and skipped silently in every sell path.
_MIN_SELL_USDT = 1.0


# FIX: _get_credentials() was called on EVERY authenticated API request,
# meaning each order check / placement triggered a Supabase read.
# With 50 orders polled every 60s that's 50+ extra DB hits per minute.
# Cache for 30s — new keys take effect within 30s of being saved.
_cred_cache: tuple[str, str, str] | None = None
_cred_cache_ts: float = 0.0
_CRED_TTL = 30.0   # seconds


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
    """Call this after saving new API keys so they take effect immediately."""
    global _cred_cache, _cred_cache_ts
    _cred_cache    = None
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
        self.base    = BITGET_BASE_URL
        self.session = requests.Session()
        self._precision_cache: dict[str, tuple[int, int]] = {}
        self._min_notional_cache: dict[str, float] = {}
        self._min_base_cache: dict[str, float] = {}  # FIX: min base qty per symbol

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> Any:
        start_ts = time.monotonic()
        qs = ""
        if params:
            qs = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        full_path = path + qs
        headers   = _auth_headers("GET", full_path)
        resp      = self.session.get(self.base + full_path, headers=headers, timeout=10)
        duration = time.monotonic() - start_ts
        if duration > 1.0:
            logger.warning("Slow BitGet GET %s took %.2fs", path, duration)
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
        headers  = _auth_headers("POST", path, body_str)
        headers["Content-Type"] = "application/json"
        resp = self.session.post(self.base + path, headers=headers, data=body_str, timeout=10)
        if not resp.ok:
            body_txt = resp.text[:400]
            logger.error("BitGet POST %s → %s: %s", path, resp.status_code, body_txt)
            raise RuntimeError(f"HTTP {resp.status_code}: {body_txt}")
        data = resp.json()
        if data.get("code") != "00000":
            raise RuntimeError(f"[{data.get('code')}] {data.get('msg', 'BitGet API error')}")
        return data.get("data", {})

    # ── Symbol precision ──────────────────────────────────────────────────────

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
            data  = resp.json()
            items = data.get("data", []) if data.get("code") == "00000" else []
            if items:
                info = items[0]

                # FIX: BitGet v2 API uses different field names depending on the
                # endpoint version. Try all known variants so we never silently
                # fall back to an overly-permissive default and get error 40808
                # ("size checkBDScale error").
                # Known price-precision field names (try in order).
                # LOG confirmed: BitGet v2 returns 'pricePrecision' (not
                # pricePlace/priceScale/pricePrec which are all None).
                pp_raw = (info.get("pricePrecision")
                          or info.get("pricePlace")
                          or info.get("priceScale")
                          or info.get("pricePrec"))
                # Known qty-precision field names (try in order).
                # LOG confirmed: BitGet v2 returns 'quantityPrecision'.
                qp_raw = (info.get("quantityPrecision")
                          or info.get("quantityPlace")
                          or info.get("quantityScale")
                          or info.get("basePrecision"))

                # FIX: Ensure we never use 0 precision unless explicitly returned
                pp = int(pp_raw) if pp_raw is not None else 2
                qp = int(qp_raw) if qp_raw is not None else 2

                # ── FIX: Hardcoded overrides for problematic symbols ─────────────
                # Some new symbols like VIRTUAL or PI might return inconsistent
                # precision data from public endpoints.
                if "VIRTUAL" in symbol: pp, qp = 4, 0
                if "PI" in symbol:      pp, qp = 2, 1
                if "ACN" in symbol:     pp, qp = 6, 0

                # Cache min notional (USDT) while we have the symbol info
                mn_raw = (info.get("minTradeUSDT")
                          or info.get("minTradeAmount")
                          or info.get("minOrderAmt"))
                if mn_raw is not None:
                    try:
                        self._min_notional_cache[symbol] = float(mn_raw)
                    except (TypeError, ValueError):
                        pass

                # FIX: Cache min BASE quantity — BitGet enforces a per-symbol
                # minimum base-token quantity AND a USDT notional floor.  For
                # BTC the minimum is 0.0001 BTC; holding 0.00004 BTC passes
                # the notional check (~$4 > $1) but the sell is rejected with
                # 43012 because the base qty is below the exchange minimum.
                # Try every plausible field name — we log the full dict below
                # to discover the actual field name BitGet uses.
                mb_raw = (info.get("minTradeNum")
                          or info.get("baseSizeMin")
                          or info.get("minBaseTradeSize")
                          or info.get("minQty")
                          or info.get("minBuyAmount")
                          or info.get("minSellAmount")
                          or info.get("minOrderSize")
                          or info.get("minBaseSz")
                          or info.get("baseMinSize")
                          or info.get("lotSz")
                          or info.get("minSz")
                          or info.get("minTradeAmount"))
                if mb_raw is not None:
                    try:
                        self._min_base_cache[symbol] = float(mb_raw)
                    except (TypeError, ValueError):
                        pass

                # DIAGNOSTIC: log the FULL symbol info dict so we can find
                # the exact field name BitGet uses for minimum base quantity.
                # Once confirmed, we can narrow this log down.
                logger.info(
                    "get_symbol_precision(%s): pp=%d qp=%d min_base_cached=%.8f | FULL_INFO=%s",
                    symbol, pp, qp,
                    self._min_base_cache.get(symbol, 0.0),
                    dict(info),
                )
                self._precision_cache[symbol] = (pp, qp)
                return pp, qp
        except Exception as e:
            logger.warning("get_symbol_precision(%s): %s — using defaults", symbol, e)
        self._precision_cache[symbol] = (2, 4)  # safe defaults: not (2,6)
        return 2, 4

    def get_min_notional(self, symbol: str) -> float:
        """Return the minimum USDT value per order for *symbol* from BitGet.

        Calls get_symbol_precision first (which populates _min_notional_cache
        as a side-effect), so there is no extra HTTP request if precision was
        already fetched.  Falls back to 1.0 USDT if the exchange does not
        publish a minimum — callers should treat 1.0 as "no real floor".
        """
        if symbol not in self._min_notional_cache:
            self.get_symbol_precision(symbol)   # populates cache as side-effect
        return self._min_notional_cache.get(symbol, 1.0)

    def get_min_base_qty(self, symbol: str) -> float:
        """Return the minimum BASE-token quantity per order for *symbol*.

        FIX: BitGet reuses error 43012 "Insufficient balance" when the order
        qty is below the exchange minimum base quantity (e.g. 0.0001 BTC).
        We check this BEFORE attempting the sell to classify tiny holdings as
        dust and skip them gracefully rather than reporting an error.

        Falls back to 0.0 when the exchange does not publish a minimum.
        """
        if symbol not in self._min_base_cache:
            self.get_symbol_precision(symbol)   # populates cache as side-effect
        return self._min_base_cache.get(symbol, 0.0)

    # ── Credentials ───────────────────────────────────────────────────────────

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
                hint = "API Key غير موجود أو تم حذفه من BitGet."
            elif "40007" in err:
                hint = "التوقيع خاطئ — تأكد من صحة API Secret."
            elif "40011" in err:
                hint = "Passphrase خاطئ — تأكد من الـ Passphrase الذي أدخلته."
            elif "40006" in err:
                hint = "انتهت صلاحية الطلب — تحقق من ضبط الوقت في الخادم."
            elif "40309" in err or "ip" in err.lower():
                hint = "عنوان IP غير مسموح به — أزل قيود IP من إعدادات API على BitGet."
            elif "43114" in err or "permission" in err.lower():
                hint = "صلاحيات غير كافية — تأكد من تفعيل Read + Spot Trade."
            elif "400" in err:
                hint = "خطأ في تنسيق الطلب — تأكد أن API Key/Secret/Passphrase صحيحة."
            else:
                hint = err
            return False, hint
        except Exception as e:
            return False, f"خطأ في الاتصال بـ BitGet: {e}"

    # ── Market data (public) ───────────────────────────────────────────────────

    def get_ticker(self, symbol: str) -> dict:
        resp = self.session.get(
            self.base + "/api/v2/spot/market/tickers",
            params={"symbol": symbol},
            timeout=10,
        )
        resp.raise_for_status()
        data   = resp.json()
        result = data.get("data", [])
        if data.get("code") == "00000" and result:
            return result[0]
        raise RuntimeError(f"الزوج {symbol} غير موجود على BitGet")

    def get_price(self, symbol: str) -> float:
        return float(self.get_ticker(symbol)["lastPr"])

    def get_all_tickers(self) -> dict[str, float]:
        """Return {coin: price_in_usdt} for every *USDT spot pair in one API call."""
        resp = self.session.get(
            self.base + "/api/v2/spot/market/tickers",
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        prices: dict[str, float] = {}
        for t in data.get("data", []):
            sym = t.get("symbol", "")
            if sym.endswith("USDT"):
                coin = sym[:-4]          # "BTCUSDT" → "BTC"
                try:
                    prices[coin] = float(t["lastPr"])
                except (KeyError, ValueError):
                    pass
        return prices

    def get_market_stats(self, symbols: list[str]) -> dict[str, dict]:
        """Return full market stats for given symbols in one API call.
        Fields per symbol: price, change24h (0.032 = +3.2%), high24h, low24h,
        vol_usdt (24h USDT volume), range24h_pct ((high-low)/low*100).
        """
        resp = self.session.get(
            self.base + "/api/v2/spot/market/tickers",
            timeout=10,
        )
        resp.raise_for_status()
        sym_set = set(symbols)
        result: dict[str, dict] = {}
        for t in resp.json().get("data", []):
            sym = t.get("symbol", "")
            if sym not in sym_set:
                continue
            try:
                high = float(t.get("high24h", 0))
                low  = float(t.get("low24h",  0))
                pr   = float(t.get("lastPr",  0))
                range_pct = ((high - low) / low * 100) if low > 0 else 0.0
                result[sym] = {
                    "price":        pr,
                    "change24h":    float(t.get("change24h", 0)),
                    "high24h":      high,
                    "low24h":       low,
                    "vol_usdt":     float(t.get("usdtVol", t.get("quoteVolume", 0))),
                    "range24h_pct": range_pct,
                }
            except (ValueError, TypeError):
                pass
        return result

    def get_symbols(self) -> list[str]:
        try:
            resp = self.session.get(self.base + "/api/v2/spot/public/symbols", timeout=10)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [d["symbol"] for d in data if isinstance(d, dict) and "symbol" in d]
        except Exception as e:
            logger.warning("get_symbols error: %s", e)
            return []

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_balance(self) -> list[dict]:
        result = self._get("/api/v2/spot/account/assets")
        if isinstance(result, list):   return result
        if isinstance(result, dict):   return result.get("assets", result.get("list", []))
        return []

    # ── Trading ───────────────────────────────────────────────────────────────

    def place_limit_order(
        self, symbol: str, side: str,
        price: float, qty: float,
        price_places: int = 2, qty_places: int = 6,
    ) -> dict:
        return self._post("/api/v2/spot/trade/place-order", {
            "symbol":    symbol,
            "side":      side,
            "orderType": "limit",
            "force":     "gtc",
            "price":     f"{price:.{price_places}f}",
            "size":      f"{qty:.{qty_places}f}",
        })

    def place_market_sell(self, symbol: str, qty: float, qty_places: int = 6) -> dict:
        """Sell `qty` of base currency at market price immediately.

        FIX: Changed force from 'fok' (Fill or Kill) to 'ioc' (Immediate or
        Cancel).  FOK rejects the *entire* order if the full qty cannot be
        filled at once — which happens whenever there is a tiny timing gap
        between our balance read and the order submission (e.g. a fraction of
        a token is still frozen).  IOC fills whatever it can and cancels the
        remainder, so a partial fill still succeeds.
        """
        return self._post("/api/v2/spot/trade/place-order", {
            "symbol":    symbol,
            "side":      "sell",
            "orderType": "market",
            "force":     "ioc",
            "size":      f"{qty:.{qty_places}f}",
        })

    def place_market_buy_usdt(self, symbol: str, usdt_amount: float) -> dict:
        """Buy base currency at market price using `usdt_amount` USDT.

        BitGet market-buy orders interpret `size` as the **quote** (USDT) amount,
        NOT the base-token quantity.  Passing token qty here would spend that
        many USDT instead of buying that many tokens — the original bug.
        """
        return self._post("/api/v2/spot/trade/place-order", {
            "symbol":    symbol,
            "side":      "buy",
            "orderType": "market",
            "force":     "fok",
            "size":      f"{usdt_amount:.2f}",   # USDT, always 2 d.p.
        })

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        return self._post("/api/v2/spot/trade/cancel-order", {
            "symbol":  symbol,
            "orderId": order_id,
        })

    def get_order(self, symbol: str, order_id: str) -> dict:
        return self._get("/api/v2/spot/trade/order-info", {
            "symbol":  symbol,
            "orderId": order_id,
        })

    def get_history_order(self, symbol: str, order_id: str) -> dict | None:
        """
        Look up a single order in the filled/cancelled history endpoint.
        Returns the order dict if found, or None.
        BitGet uses /api/v2/spot/trade/history-orders filtered by orderId.
        """
        try:
            data = self._get("/api/v2/spot/trade/history-orders", {
                "symbol":  symbol,
                "orderId": order_id,
                "limit":   "1",
            })
            orders: list = []
            if isinstance(data, list):
                orders = data
            elif isinstance(data, dict):
                orders = data.get("orderList", data.get("list", []))
            for o in orders:
                if str(o.get("orderId", "")) == str(order_id):
                    return o
            return None
        except Exception as e:
            logger.warning("get_history_order(%s, %s) failed: %s", symbol, order_id, e)
            return None

    def get_open_orders(self, symbol: str) -> list[dict]:
        data = self._get("/api/v2/spot/trade/unfilled-orders", {"symbol": symbol})
        if isinstance(data, list): return data
        if isinstance(data, dict): return data.get("orderList", data.get("list", []))
        return []

    def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all open orders for symbol. Returns count cancelled."""
        orders  = self.get_open_orders(symbol)
        count   = 0
        for o in orders:
            try:
                self.cancel_order(symbol, o["orderId"])
                count += 1
            except Exception as e:
                logger.warning("Cancel order %s failed: %s", o.get("orderId"), e)
        return count

    def cancel_symbol_orders_batch(self, symbol: str) -> bool:
        """Cancel ALL open orders for a symbol — guaranteed, with verification.

        Strategy:
          1. Try the BitGet batch endpoint first (fast, but may silently no-op
             on some account types or if the endpoint doesn't exist for spot).
          2. ALWAYS follow up by fetching remaining open orders and cancelling
             them one-by-one with a high limit (200) and pagination loop —
             this is the only way to guarantee the balance is fully unfrozen.

        The old approach (batch-only with no verification) returned True even
        when BitGet returned 00000 without actually cancelling anything, leaving
        the balance frozen and causing 43012 on every subsequent sell attempt.
        """
        # ── Step 1: attempt the batch endpoint (best-effort) ─────────────────
        try:
            self._post("/api/v2/spot/trade/cancel-symbol-order", {"symbol": symbol})
            logger.info("cancel_symbol_orders_batch: batch endpoint OK for %s", symbol)
        except Exception as e:
            logger.warning(
                "cancel_symbol_orders_batch: batch endpoint failed for %s (%s) "
                "— will clean up individually",
                symbol, e,
            )

        # ── Step 2: verify + clean up any remaining orders (with pagination) ──
        # Fetch remaining open orders with a high limit (200) and cancel them.
        # Loop breaks as soon as: list is empty OR no progress was made this page
        # (avoids infinite loop when orders can't be cancelled for any reason).
        total_cancelled = 0
        for _page in range(5):   # safety cap: 5 passes max
            try:
                data = self._get(
                    "/api/v2/spot/trade/unfilled-orders",
                    {"symbol": symbol, "limit": "200"},
                )
            except Exception as e:
                logger.warning(
                    "cancel_symbol_orders_batch: fetch remaining orders failed for %s: %s",
                    symbol, e,
                )
                break

            orders = (data if isinstance(data, list)
                      else data.get("orderList", data.get("list", []))
                      if isinstance(data, dict) else [])
            if not orders:
                break   # nothing left — done

            cancelled_this_page = 0
            for o in orders:
                try:
                    self.cancel_order(symbol, o["orderId"])
                    total_cancelled += 1
                    cancelled_this_page += 1
                except Exception as e:
                    logger.warning(
                        "cancel_symbol_orders_batch: cancel order %s failed: %s",
                        o.get("orderId"), e,
                    )

            if cancelled_this_page == 0:
                # Got orders but couldn't cancel any — stop to avoid infinite loop
                logger.warning(
                    "cancel_symbol_orders_batch: %d orders remain for %s but none "
                    "could be cancelled (may be in non-cancellable state)",
                    len(orders), symbol,
                )
                break

        logger.info(
            "cancel_symbol_orders_batch: done — %d orders cancelled for %s",
            total_cancelled, symbol,
        )
        return True

    def close_all_at_market(self, symbols: list[str]) -> dict:
        """
        For each symbol:
          1. Cancel all open limit orders
          2. Sell any available base-currency balance at market price
        Returns a summary dict.
        """
        cancelled_orders = 0
        market_sells: list[str] = []
        errors: list[str]       = []

        # Cancel all open orders first — use batch endpoint to avoid the 20-order
        # pagination limit of unfilled-orders; cancels EVERYTHING atomically.
        for sym in set(symbols):
            try:
                self.cancel_symbol_orders_batch(sym)
                cancelled_orders += 1
            except Exception as e:
                errors.append(f"إلغاء أوامر {sym}: {e}")

        # FIX: Wait for frozen balances to release before reading available balance.
        # BitGet does not release frozen balance instantly after cancellation.
        # Poll up to 15 s; stop as soon as all target coins have frozen == 0.
        if cancelled_orders > 0:
            logger.info("close_all_at_market: waiting %d seconds for frozen balances to release", 5)
            time.sleep(5)
            target_coins = set()
            for sym in set(symbols):
                base = sym[:-4] if sym.endswith("USDT") else sym
                target_coins.add(base)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    assets = self.get_account_balance()
                    still_frozen = {
                        (a.get("coin") or a.get("coinName", ""))
                        for a in assets
                        if (a.get("coin") or a.get("coinName", "")) in target_coins
                        and float(a.get("frozen", 0)) + float(a.get("locked", 0)) > 1e-8
                        and float(a.get("available", 0)) < 1e-8
                    }
                    if not still_frozen:
                        logger.info("close_all_at_market: frozen balances cleared")
                        break
                    logger.info("close_all_at_market: waiting — still frozen: %s", still_frozen)
                    time.sleep(2)
                except Exception:
                    break

        # Get account balance and sell any base currency we hold
        try:
            assets = self.get_account_balance()
            asset_map = {
                a.get("coin", a.get("coinName", "")): float(a.get("available", 0))
                for a in assets
            }
            for sym in set(symbols):
                if sym.endswith("USDT"):
                    base = sym[:-4]   # e.g. BTCUSDT → BTC
                elif sym.endswith("BTC"):
                    base = sym[:-3]
                else:
                    base = sym

                available = asset_map.get(base, 0)
                if available < 0.000001:
                    continue

                try:
                    pp, qp = self.get_symbol_precision(sym)
                    price  = self.get_price(sym)
                    # FIX: use floor (truncate), not round — rounding UP causes
                    # BitGet to reject with 43012 because we ask to sell more
                    # than the actual available balance.
                    qty    = math.floor(available * 10**qp) / 10**qp

                    # FIX: Check if the value is above minimum notional before selling.
                    # Selling "dust" causes the entire batch to fail or returns annoying errors.
                    # Hard dust filter — skip anything worth < $1 USDT.
                    if (qty * price) < _MIN_SELL_USDT:
                        logger.info(
                            "Skipping market sell for %s: value %.4f USDT < $%.1f minimum",
                            sym, qty * price, _MIN_SELL_USDT,
                        )
                        continue

                    if qty <= 0:
                        continue

                    self.place_market_sell(sym, qty, qty_places=qp)
                    market_sells.append(f"{qty:.{qp}f} {base} ({sym})")
                    logger.info("Market sell: %s qty=%s", sym, f"{qty:.{qp}f}")
                except Exception as e:
                    errors.append(f"بيع {sym}: {e}")
        except Exception as e:
            errors.append(f"جلب الرصيد: {e}")

        return {
            "cancelled_orders": cancelled_orders,
            "market_sells":     market_sells,
            "errors":           errors,
        }


    def liquidate_wallet(self) -> dict:
        """Sell every non-USDT asset in the spot wallet at market price.

        Steps:
          1. Read FULL wallet (available + frozen).
          2. Cancel ALL open orders for every coin that has any balance
             (this releases frozen amounts back to available).
          3. Poll balance until frozen clears (max 15 s).
          4. Re-read wallet and sell ONLY available balance for each coin.

        Unlike close_all_at_market (which only touches symbols tied to active
        grid sessions), this sweeps the ENTIRE wallet — including coins held
        outside any grid session.  Emergency kill-switch.

        Returns:
            sold             — list of "qty COIN" strings for successful sells
            skipped          — list of coins skipped (dust / no USDT pair / frozen)
            errors           — list of error strings
            cancelled_orders — total open orders cancelled
            usdt_before      — USDT balance before the sweep
        """
        sold:    list[str] = []
        skipped: list[str] = []
        errors:  list[str] = []
        cancelled_orders = 0

        # ── Step 1: read full wallet (available + frozen) ─────────────────────
        try:
            assets = self.get_account_balance()
        except Exception as e:
            return {
                "sold": [], "skipped": [], "errors": [f"جلب الرصيد: {e}"],
                "cancelled_orders": 0, "usdt_before": 0.0,
            }

        # Snapshot USDT before we start
        usdt_before = 0.0
        for a in assets:
            if (a.get("coin") or a.get("coinName", "")) == "USDT":
                usdt_before = float(a.get("available", 0))
                break

        # Collect every non-USDT coin that has ANY balance (available OR frozen)
        coins_to_process: list[str] = []
        for a in assets:
            coin      = a.get("coin") or a.get("coinName", "")
            available = float(a.get("available", 0))
            frozen    = float(a.get("frozen",    0))
            locked    = float(a.get("locked",    0))
            if not coin or coin == "USDT":
                continue
            if available + frozen + locked < 1e-8:
                continue
            coins_to_process.append(coin)

        # ── Step 2: cancel all open orders to release frozen balance ──────────
        # FIX: use batch cancel (cancel-symbol-order) instead of page-by-page
        # cancel_all_orders.  The old approach fetched unfilled-orders which has
        # a default page size of ~20; if the grid placed more orders (e.g. 30+),
        # the excess were never cancelled and the balance stayed frozen.
        # cancel_symbol_orders_batch() cancels ALL orders atomically in one call.
        for coin in coins_to_process:
            symbol = f"{coin}USDT"
            try:
                ok = self.cancel_symbol_orders_batch(symbol)
                if ok:
                    cancelled_orders += 1   # count symbols cancelled, not orders
            except Exception as e:
                logger.debug("Liquidate wallet: batch cancel %s → %s", symbol, e)

        # ── Step 3: Poll until frozen balances clear (max 20 s) ─────────────
        # BitGet does NOT release frozen balance instantly after order cancellation.
        # 3 s was not enough for exchanges with many orders; poll until clear.
        time.sleep(3)   # initial short wait
        target_coins = set(coins_to_process)
        deadline = time.monotonic() + 17   # up to 17 more seconds (20 s total)
        while time.monotonic() < deadline:
            try:
                probe = self.get_account_balance()
                still_frozen = {
                    (a.get("coin") or a.get("coinName", ""))
                    for a in probe
                    if (a.get("coin") or a.get("coinName", "")) in target_coins
                    and float(a.get("available", 0)) < 1e-8
                    and float(a.get("frozen", 0)) + float(a.get("locked", 0)) > 1e-8
                }
                if not still_frozen:
                    logger.info("Liquidate wallet: all frozen balances cleared")
                    break
                logger.info("Liquidate wallet: waiting — still frozen: %s", still_frozen)
                time.sleep(2)
            except Exception:
                break

        # ── Step 4: Final balance re-read then sell everything ───────────────
        # Always do a fresh read right before selling so we use the latest
        # available amounts after the freeze-poll loop above.
        try:
            assets = self.get_account_balance()
            logger.info("Liquidate wallet: final balance read OK — %d assets", len(assets))
        except Exception as e:
            logger.warning("Liquidate wallet: final balance re-read failed (%s) — using last known", e)

        for a in assets:
            coin      = a.get("coin") or a.get("coinName", "")
            available = float(a.get("available", 0))
            frozen    = float(a.get("frozen", 0) or a.get("locked", 0))

            if not coin or coin == "USDT":
                continue

            total_qty = available + frozen
            if total_qty < 1e-8:
                continue

            # FIX: Only sell the AVAILABLE balance — never attempt to sell frozen
            # amounts.  Previously the code used (available + frozen) as the sell
            # qty, which caused 43012 "Insufficient balance" whenever frozen > 0
            # because BitGet only lets you spend the available portion.
            if available < 1e-8:
                # Balance is entirely frozen even after the 15 s wait.
                # This can happen when the balance is locked by an external bot,
                # a different API key, or a BitGet-side hold.  We cannot sell it.
                skipped.append(f"{coin} (رصيد مجمد بالكامل — تحقق من الأوامر المفتوحة)")
                logger.info(
                    "Liquidate wallet: skipped %s — available=0 frozen=%.8f after timeout",
                    coin, frozen,
                )
                continue

            sell_qty = available  # always sell only what's available

            symbol = f"{coin}USDT"
            try:
                pp, qp = self.get_symbol_precision(symbol)
                # FIX: floor (truncate), not round — round() rounds UP and causes
                # BitGet to reject with 43012 "Insufficient balance" because we
                # ask to sell more than the actual available balance.
                # e.g. available=0.00004495 BTC → round(,6)=0.000045 > available
                qty    = math.floor(sell_qty * 10**qp) / 10**qp
                if qty <= 0:
                    skipped.append(f"{coin} (كمية صغيرة جداً بعد التقريب)")
                    continue

                # ── Price + minimum checks ─────────────────────────────────
                # BitGet returns 43012 for qty below min base qty — the same
                # error code as "no balance" — making it look like a balance
                # problem when it is actually a dust/minimum problem.
                # NOTE: minTradeNum is always None in BitGet v2 response, so
                # min_base_qty always returns 0.0.  The notional check is the
                # only active guard.  We log everything so the cause is clear.
                price        = 0.0
                min_notional = 1.0
                min_base_qty = 0.0
                try:
                    price        = self.get_price(symbol)
                    min_notional = self.get_min_notional(symbol)
                    min_base_qty = self.get_min_base_qty(symbol)
                except Exception as price_err:
                    logger.warning(
                        "Liquidate wallet: price/min fetch failed for %s: %s — selling anyway",
                        symbol, price_err,
                    )

                logger.info(
                    "Liquidate wallet: PRE-SELL %s | "
                    "available_raw=%.8f frozen_raw=%.8f | "
                    "qty=%.8f qp=%d | "
                    "price=%.4f value=%.4f USDT | "
                    "min_notional=%.4f min_base_qty=%.8f",
                    symbol, sell_qty, frozen, qty, qp,
                    price, qty * price if price else 0.0,
                    min_notional, min_base_qty,
                )

                if min_base_qty > 0 and qty < min_base_qty:
                    skipped.append(
                        f"{coin} (كمية دون الحد: {qty:.{qp}f} اقل من {min_base_qty})"
                    )
                    logger.info(
                        "Liquidate wallet: skipping %s — qty %.8f < min base %.8f",
                        symbol, qty, min_base_qty,
                    )
                    continue

                if price > 0 and (qty * price) < _MIN_SELL_USDT:
                    skipped.append(f"{coin} (dust: {qty*price:.4f} USDT اقل من $1)")
                    logger.info(
                        "Liquidate wallet: skipping %s — value %.4f USDT < $1 minimum",
                        symbol, qty * price,
                    )
                    continue

                self.place_market_sell(symbol, qty, qty_places=qp)
                sold.append(f"{qty:.{qp}f} {coin}")
                logger.info("Liquidate wallet: sold %s qty=%s", symbol, f"{qty:.{qp}f}")

            except Exception as e:
                err_str = str(e)
                logger.warning("Liquidate wallet: sell %s failed → %s", symbol, err_str[:200])

                # FIX: 43012 on the first attempt — skip retry to save time.
                if "43012" in err_str:
                    skipped.append(f"{coin} (رصيد مجمد أو دون الحد)")
                    continue

                # Only skip if the pair genuinely doesn't exist on BitGet.
                # Use specific error codes — avoid broad keyword matches that
                # catch unrelated errors (minimum notional, rate-limit, etc.).
                _no_pair = (
                    "40034" in err_str   # symbol not found / invalid symbol
                    or "40018" in err_str   # trading pair suspended / not exist
                    or "symbol not found"    in err_str.lower()
                    or "invalid symbol"      in err_str.lower()
                    or "الزوج" in err_str and "غير موجود" in err_str
                )
                _dust = (
                    "40768" in err_str   # insufficient balance (dust)
                    or "minimum" in err_str.lower()
                    or "too small"  in err_str.lower()
                    or "size"       in err_str.lower()
                )
                if _no_pair:
                    skipped.append(f"{coin} (لا يوجد زوج {symbol} على BitGet)")
                    logger.info("Liquidate wallet: skipped %s — no USDT pair", coin)
                elif _dust:
                    skipped.append(f"{coin} (كمية صغيرة جداً أو دون الحد الأدنى)")
                    logger.info("Liquidate wallet: skipped %s — dust/below min notional", coin)
                else:
                    errors.append(f"{coin}: {err_str[:120]}")

        return {
            "sold":             sold,
            "skipped":          skipped,
            "errors":           errors,
            "cancelled_orders": cancelled_orders,
            "usdt_before":      usdt_before,
        }


_client: Optional[BitGetClient] = None

def get_bitget() -> BitGetClient:
    global _client
    if _client is None:
        _client = BitGetClient()
    return _client
