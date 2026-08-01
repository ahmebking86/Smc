"""
Portfolio Rebalancing Engine — supports Bitget + MEXC.
Creates a portfolio of up to 20 coins, rebalances by time or deviation %.
Includes robust replace_asset method.
FIXED: added all_active() + load_from_db() (were missing → monitor crash)
"""

from __future__ import annotations
import uuid
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from database import db
from config import MIN_ORDER_USDT, MAX_ASSETS

logger = logging.getLogger(__name__)


@dataclass
class AssetConfig:
    symbol: str
    target_pct: float          # 0–100


@dataclass
class PortfolioConfig:
    total_investment: float
    assets: list[AssetConfig]
    rebalance_mode: str        # "time" | "percent"
    interval_hours: float = 0
    threshold_pct: float = 0
    exchange: str = "bitget"   # "bitget" | "mexc"


@dataclass
class PortfolioAsset:
    id: str
    symbol: str
    target_pct: float
    initial_qty: float = 0.0
    status: str = "active"


@dataclass
class Portfolio:
    id: str
    config: PortfolioConfig
    assets: list[PortfolioAsset] = field(default_factory=list)
    total_pnl: float = 0.0
    status: str = "active"
    last_rebalance_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    exchange: str = "bitget"


def _get_client(exchange: str = "bitget"):
    """Return the correct exchange client."""
    exchange = (exchange or "bitget").lower()
    if exchange == "mexc":
        from trading.mexc_client import get_mexc
        return get_mexc()
    from trading.bitget_client import get_bitget
    return get_bitget()


class RebalanceEngine:

    def __init__(self, exchange: str = "bitget"):
        self.exchange = exchange.lower()
        self.client = _get_client(self.exchange)
        self._portfolios: dict[str, Portfolio] = {}

    def set_exchange(self, exchange: str) -> None:
        self.exchange = exchange.lower()
        self.client = _get_client(self.exchange)

    # ── Active portfolios (FIX for monitor crash) ─────────────────────────────

    def all_active(self) -> list[Portfolio]:
        """Return active portfolios from ALL exchange engines (used by monitor)."""
        result = []
        seen = set()
        engines = list(_engines.values()) or [self]
        for eng in engines:
            for p in eng._portfolios.values():
                if p.status == "active" and p.id not in seen:
                    seen.add(p.id)
                    result.append(p)
        return result

    def load_from_db(self) -> None:
        """Load every active/paused portfolio into the correct exchange engine."""
        rows = db.list_active_portfolios()
        total = 0
        for row in rows:
            try:
                exchange = (row.get("exchange") or "bitget").lower()
                eng = get_engine(exchange)
                if eng.load_portfolio(row["id"]):
                    total += 1
            except Exception as e:
                logger.error("Failed to load portfolio %s: %s", row.get("id", "?")[:8], e)
        logger.info("Loaded %d portfolios from DB", total)


    def get_portfolio(self, portfolio_id: str) -> Optional[Portfolio]:
        """Find portfolio in this engine, any other engine, or load from DB."""
        p = self._portfolios.get(portfolio_id)
        if p:
            return p
        # search other engines (mexc vs bitget)
        for eng in list(_engines.values()):
            if eng is self:
                continue
            p = eng._portfolios.get(portfolio_id)
            if p:
                return p
        # not in memory — try DB
        row = db.get_portfolio(portfolio_id)
        if not row or row.get("status") == "closed":
            return None
        exchange = (row.get("exchange") or "bitget").lower()
        eng = get_engine(exchange)
        return eng.load_portfolio(portfolio_id)

    def all_portfolios(self) -> list[Portfolio]:
        # merge from all engines
        seen = set()
        out = []
        for eng in list(_engines.values()):
            for p in eng._portfolios.values():
                if p.id not in seen:
                    seen.add(p.id)
                    out.append(p)
        return out

    # ── Create ────────────────────────────────────────────────────────────────

    def create_portfolio(self, config: PortfolioConfig) -> Portfolio:
        self.set_exchange(config.exchange or "bitget")
        if not self.client.has_credentials():
            raise RuntimeError(
                f"مفاتيح {(getattr(self.client, 'exchange_name', None) or self.exchange).upper()} API غير مُعيَّنة.\n"
                "اضغط 🔑 إعداد API من القائمة الرئيسية."
            )
        if len(config.assets) == 0 or len(config.assets) > MAX_ASSETS:
            raise RuntimeError(f"عدد العملات يجب أن يكون بين 1 و {MAX_ASSETS}")
        total_pct = sum(a.target_pct for a in config.assets)
        if abs(total_pct - 100.0) > 0.05:
            raise RuntimeError(f"مجموع النسب = {total_pct:.2f}% — يجب أن يكون 100%")

        portfolio_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        db.create_portfolio({
            "id":                portfolio_id,
            "total_investment":  config.total_investment,
            "rebalance_mode":    config.rebalance_mode,
            "interval_hours":    config.interval_hours,
            "threshold_pct":     config.threshold_pct,
            "status":            "active",
            "total_pnl":         0.0,
            "last_rebalance_at": now.isoformat(),
            "created_at":        now.isoformat(),
            "exchange":          self.exchange,
        })

        portfolio = Portfolio(
            id=portfolio_id,
            config=config,
            last_rebalance_at=now,
            created_at=now,
            exchange=self.exchange,
        )

        results: list[str] = []
        errors: list[str] = []

        for ac in config.assets:
            usdt = round(config.total_investment * ac.target_pct / 100.0, 2)
            if usdt < MIN_ORDER_USDT:
                errors.append(f"{ac.symbol}: المبلغ {usdt:.2f} USDT أقل من الحد الأدنى")
                continue
            try:
                price = self.client.get_price(ac.symbol)
                _, qty_places = self.client.get_symbol_precision(ac.symbol)
                self.client.place_market_buy_usdt(ac.symbol, usdt)
                est_qty = round(usdt / price * 0.997, qty_places)
                asset_id = str(uuid.uuid4())
                db.create_asset({
                    "id":           asset_id,
                    "portfolio_id": portfolio_id,
                    "symbol":       ac.symbol,
                    "target_pct":   ac.target_pct,
                    "initial_qty":  est_qty,
                    "status":       "active",
                })
                db.create_trade({
                    "id":           str(uuid.uuid4()),
                    "portfolio_id": portfolio_id,
                    "symbol":       ac.symbol,
                    "side":         "buy",
                    "usdt_amount":  usdt,
                    "qty":          est_qty,
                    "price":        price,
                })
                portfolio.assets.append(PortfolioAsset(
                    id=asset_id,
                    symbol=ac.symbol,
                    target_pct=ac.target_pct,
                    initial_qty=est_qty,
                ))
                results.append(f"✅ {ac.symbol}: اشتريت بـ {usdt:.2f} USDT")
                time.sleep(0.4)
            except Exception as e:
                logger.error("Initial buy %s failed: %s", ac.symbol, e)
                errors.append(f"❌ {ac.symbol}: {e}")

        if not portfolio.assets:
            db.close_portfolio(portfolio_id, 0)
            raise RuntimeError("تعذّر شراء أي عملة.\n" + "\n".join(errors))

        self._portfolios[portfolio_id] = portfolio
        portfolio._create_results = results  # type: ignore
        portfolio._create_errors = errors    # type: ignore
        logger.info(
            "Portfolio %s created on %s | %d assets | invest=%.2f",
            portfolio_id[:8], self.exchange, len(portfolio.assets), config.total_investment,
        )
        return portfolio

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def _get_balances(self) -> dict[str, float]:
        balances: dict[str, float] = {}
        try:
            raw = self.client.get_account_balance()
            for item in raw:
                coin = (item.get("coin") or item.get("currency") or item.get("asset") or "").upper()
                avail = float(
                    item.get("available")
                    or item.get("availableBalance")
                    or item.get("free")
                    or 0
                )
                if coin and avail > 0:
                    balances[coin] = avail
        except Exception as e:
            logger.error("get_account_balance failed: %s", e)
        return balances

    def snapshot(self, portfolio: Portfolio) -> dict:
        # Ensure we use the correct client for this portfolio
        exch = getattr(portfolio, "exchange", None) or portfolio.config.exchange or "bitget"
        if exch != self.exchange:
            self.set_exchange(exch)

        balances = self._get_balances()
        prices: dict[str, float] = {}
        rows = []
        total_value = 0.0

        for asset in portfolio.assets:
            if asset.status != "active":
                continue
            coin = asset.symbol.replace("USDT", "")
            try:
                price = self.client.get_price(asset.symbol)
            except Exception:
                price = 0.0
            prices[asset.symbol] = price
            qty = balances.get(coin, 0.0)
            value = qty * price
            total_value += value
            rows.append({
                "symbol": asset.symbol,
                "coin": coin,
                "target_pct": asset.target_pct,
                "qty": qty,
                "price": price,
                "value": value,
                "current_pct": 0.0,
                "deviation": 0.0,
            })

        for r in rows:
            r["current_pct"] = (r["value"] / total_value * 100) if total_value > 0 else 0.0
            r["deviation"] = r["current_pct"] - r["target_pct"]

        return {
            "total_value": total_value,
            "assets": rows,
            "prices": prices,
            "balances": balances,
        }

    # ── Rebalance ─────────────────────────────────────────────────────────────

    def should_rebalance(self, portfolio: Portfolio) -> bool:
        if portfolio.status != "active":
            return False
        cfg = portfolio.config

        if cfg.rebalance_mode == "time":
            if not portfolio.last_rebalance_at:
                return True
            elapsed = datetime.now(timezone.utc) - portfolio.last_rebalance_at
            return elapsed >= timedelta(hours=cfg.interval_hours)

        if cfg.rebalance_mode == "percent":
            snap = self.snapshot(portfolio)
            for a in snap["assets"]:
                if abs(a["deviation"]) >= cfg.threshold_pct:
                    return True
            return False
        return False

    def rebalance(self, portfolio: Portfolio) -> dict:
        snap = self.snapshot(portfolio)
        total = snap["total_value"]
        if total < MIN_ORDER_USDT * 2:
            return {"actions": [], "errors": ["قيمة المحفظة صغيرة جداً لإعادة التوازن"], "total_value": total}

        actions: list[str] = []
        errors: list[str] = []

        # Sell excess
        for a in snap["assets"]:
            excess_pct = a["deviation"]
            if excess_pct <= 0.15:
                continue
            excess_usdt = total * excess_pct / 100.0
            if excess_usdt < MIN_ORDER_USDT:
                continue
            price = a["price"]
            if price <= 0:
                continue
            _, qty_places = self.client.get_symbol_precision(a["symbol"])
            sell_qty = math.floor((excess_usdt / price) * 10**qty_places) / 10**qty_places
            sell_qty = min(sell_qty, a["qty"])
            if sell_qty * price < MIN_ORDER_USDT:
                continue
            try:
                self.client.place_market_sell(a["symbol"], sell_qty, qty_places)
                db.create_trade({
                    "id": str(uuid.uuid4()),
                    "portfolio_id": portfolio.id,
                    "symbol": a["symbol"],
                    "side": "sell",
                    "usdt_amount": round(sell_qty * price, 2),
                    "qty": sell_qty,
                    "price": price,
                })
                actions.append(f"🔴 بيع {a['coin']}: {sell_qty} ≈ {sell_qty*price:.2f} USDT")
                time.sleep(0.5)
            except Exception as e:
                logger.error("Rebalance sell %s: %s", a["symbol"], e)
                errors.append(f"بيع {a['coin']}: {e}")

        if actions:
            time.sleep(1.5)

        balances = self._get_balances()
        usdt_available = balances.get("USDT", 0.0)

        # Buy deficits
        for a in snap["assets"]:
            deficit_pct = -a["deviation"]
            if deficit_pct <= 0.15:
                continue
            need_usdt = total * deficit_pct / 100.0
            need_usdt = min(need_usdt, usdt_available)
            if need_usdt < MIN_ORDER_USDT:
                continue
            need_usdt = round(need_usdt, 2)
            try:
                price = self.client.get_price(a["symbol"])
                _, qty_places = self.client.get_symbol_precision(a["symbol"])
                self.client.place_market_buy_usdt(a["symbol"], need_usdt)
                est_qty = round(need_usdt / price * 0.997, qty_places)
                db.create_trade({
                    "id": str(uuid.uuid4()),
                    "portfolio_id": portfolio.id,
                    "symbol": a["symbol"],
                    "side": "buy",
                    "usdt_amount": need_usdt,
                    "qty": est_qty,
                    "price": price,
                })
                actions.append(f"🟢 شراء {a['coin']}: ≈ {need_usdt:.2f} USDT")
                usdt_available -= need_usdt
                time.sleep(0.5)
            except Exception as e:
                logger.error("Rebalance buy %s: %s", a["symbol"], e)
                errors.append(f"شراء {a['coin']}: {e}")

        now = datetime.now(timezone.utc)
        portfolio.last_rebalance_at = now
        db.update_portfolio(portfolio.id, {"last_rebalance_at": now.isoformat()})

        return {"actions": actions, "errors": errors, "total_value": total}

    # ── Replace Asset (FIXED) ─────────────────────────────────────────────────

    def replace_asset(self, portfolio: Portfolio, old_symbol: str, new_symbol: str) -> dict:
        """
        Robust replace: sell old → wait → check USDT → buy new → update DB.
        """
        old_symbol = old_symbol.upper().strip()
        new_symbol = new_symbol.upper().strip()
        if not old_symbol.endswith("USDT"):
            old_symbol += "USDT"
        if not new_symbol.endswith("USDT"):
            new_symbol += "USDT"

        if old_symbol == new_symbol:
            raise RuntimeError("العملة القديمة والجديدة متشابهتان")

        # Ensure correct exchange client
        exch = getattr(portfolio, "exchange", None) or portfolio.config.exchange or "bitget"
        if exch != self.exchange:
            self.set_exchange(exch)

        old_asset = None
        for a in portfolio.assets:
            if a.symbol == old_symbol and a.status == "active":
                old_asset = a
                break
        if not old_asset:
            raise RuntimeError(f"العملة {old_symbol} غير موجودة أو غير نشطة في المحفظة")

        for a in portfolio.assets:
            if a.symbol == new_symbol and a.status == "active":
                raise RuntimeError(f"العملة {new_symbol} موجودة بالفعل في المحفظة")

        # Validate new symbol exists
        try:
            new_price = self.client.get_price(new_symbol)
            if new_price <= 0:
                raise RuntimeError("سعر غير صالح")
        except Exception as e:
            raise RuntimeError(f"تعذر جلب سعر {new_symbol} على {self.client.exchange_name}: {e}")

        snap = self.snapshot(portfolio)
        old_row = next((r for r in snap["assets"] if r["symbol"] == old_symbol), None)
        if not old_row or old_row["qty"] <= 0:
            raise RuntimeError(f"لا يوجد رصيد متاح لـ {old_symbol}")

        usdt_value = old_row["value"]
        if usdt_value < MIN_ORDER_USDT:
            raise RuntimeError(f"قيمة {old_symbol} صغيرة جداً ({usdt_value:.2f} USDT)")

        actions: list[str] = []
        errors: list[str] = []

        # 1) Sell old
        try:
            _, qty_places = self.client.get_symbol_precision(old_symbol)
            sell_qty = old_row["qty"]
            # Leave a tiny dust buffer if needed
            min_base = self.client.get_min_base_qty(old_symbol)
            if min_base > 0 and sell_qty < min_base:
                raise RuntimeError(f"الكمية أقل من الحد الأدنى للبيع ({min_base})")
            self.client.place_market_sell(old_symbol, sell_qty, qty_places)
            db.create_trade({
                "id": str(uuid.uuid4()),
                "portfolio_id": portfolio.id,
                "symbol": old_symbol,
                "side": "sell",
                "usdt_amount": round(usdt_value, 2),
                "qty": sell_qty,
                "price": old_row["price"],
            })
            actions.append(f"🔴 بيع {old_symbol.replace('USDT','')}: {sell_qty} ≈ {usdt_value:.2f} USDT")
        except Exception as e:
            raise RuntimeError(f"فشل بيع {old_symbol}: {e}")

        # 2) Wait for balance to settle
        time.sleep(2.0)

        # 3) Re-check available USDT
        balances = self._get_balances()
        usdt_available = balances.get("USDT", 0.0)
        buy_usdt = round(min(usdt_value * 0.996, usdt_available * 0.995), 2)
        if buy_usdt < MIN_ORDER_USDT:
            errors.append(
                f"بعد البيع، الرصيد المتاح ({usdt_available:.2f} USDT) أقل من الحد الأدنى. "
                "تم البيع لكن لم يتم الشراء."
            )
            # Still mark old as closed
            try:
                db.deactivate_asset(old_asset.id)
                old_asset.status = "closed"
            except Exception:
                pass
            return {"actions": actions, "errors": errors, "success": False}

        # 4) Buy new
        try:
            self.client.place_market_buy_usdt(new_symbol, buy_usdt)
            _, qty_places = self.client.get_symbol_precision(new_symbol)
            est_qty = round(buy_usdt / new_price * 0.997, qty_places)
            db.create_trade({
                "id": str(uuid.uuid4()),
                "portfolio_id": portfolio.id,
                "symbol": new_symbol,
                "side": "buy",
                "usdt_amount": buy_usdt,
                "qty": est_qty,
                "price": new_price,
            })
            actions.append(f"🟢 شراء {new_symbol.replace('USDT','')}: ≈ {buy_usdt:.2f} USDT")
        except Exception as e:
            errors.append(f"فشل شراء {new_symbol}: {e}")
            # Old already sold — mark closed anyway
            try:
                db.deactivate_asset(old_asset.id)
                old_asset.status = "closed"
            except Exception:
                pass
            return {"actions": actions, "errors": errors, "success": False}

        # 5) Update DB + memory
        try:
            db.deactivate_asset(old_asset.id)
        except Exception as e:
            logger.warning("deactivate_asset: %s", e)
        old_asset.status = "closed"

        new_asset_id = str(uuid.uuid4())
        try:
            db.create_asset({
                "id": new_asset_id,
                "portfolio_id": portfolio.id,
                "symbol": new_symbol,
                "target_pct": old_asset.target_pct,
                "initial_qty": est_qty,
                "status": "active",
            })
        except Exception as e:
            logger.error("create_asset after replace: %s", e)
            errors.append(f"تم التداول لكن فشل تحديث قاعدة البيانات: {e}")

        portfolio.assets.append(PortfolioAsset(
            id=new_asset_id,
            symbol=new_symbol,
            target_pct=old_asset.target_pct,
            initial_qty=est_qty,
            status="active",
        ))

        return {
            "actions": actions,
            "errors": errors,
            "success": len(errors) == 0,
            "old_symbol": old_symbol,
            "new_symbol": new_symbol,
            "usdt_value": usdt_value,
        }


    # ── Add / Remove / Funds (NEW) ─────────────────────────────────────────────

    def add_asset(self, portfolio: Portfolio, symbol: str, usdt_amount: float) -> dict:
        """Buy a new asset and add it to the portfolio."""
        actions, errors = [], []
        exch = getattr(portfolio, "exchange", None) or getattr(portfolio.config, "exchange", "bitget")
        if exch != self.exchange:
            self.set_exchange(exch)

        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol += "USDT"

        # Check if already exists
        for a in portfolio.assets:
            if a.symbol == symbol and a.status == "active":
                return {"actions": [], "errors": [f"{symbol} موجودة بالفعل"], "success": False}

        try:
            price = self.client.get_price(symbol)
            if not price or price <= 0:
                raise ValueError(f"سعر غير صالح لـ {symbol}")
        except Exception as e:
            return {"actions": [], "errors": [f"فشل جلب السعر: {e}"], "success": False}

        qty = usdt_amount / price
        try:
            order = self.client.place_market_order(symbol, "buy", usdt_amount)
            actions.append(f"تم شراء {symbol} بمبلغ {usdt_amount:.2f} USDT")
        except Exception as e:
            errors.append(f"فشل الشراء: {e}")
            return {"actions": actions, "errors": errors, "success": False}

        # Calculate new target_pct (equal weight roughly)
        active = [a for a in portfolio.assets if a.status == "active"]
        n = len(active) + 1
        new_pct = round(100.0 / n, 2)

        # Optionally re-scale existing targets
        for a in active:
            a.target_pct = round(a.target_pct * (n - 1) / n, 2)

        new_asset_id = str(uuid.uuid4())
        try:
            db.create_asset({
                "id": new_asset_id,
                "portfolio_id": portfolio.id,
                "symbol": symbol,
                "target_pct": new_pct,
                "initial_qty": qty,
                "status": "active",
            })
            # update other assets target_pct in DB
            for a in active:
                try:
                    db.update_asset(a.id, {"target_pct": a.target_pct})
                except Exception:
                    pass
        except Exception as e:
            errors.append(f"تم الشراء لكن فشل تحديث DB: {e}")

        portfolio.assets.append(PortfolioAsset(
            id=new_asset_id,
            symbol=symbol,
            target_pct=new_pct,
            initial_qty=qty,
            status="active",
        ))
        portfolio.config.total_investment = float(portfolio.config.total_investment or 0) + usdt_amount
        try:
            db.update_portfolio(portfolio.id, {"total_investment": portfolio.config.total_investment})
        except Exception:
            pass

        actions.append(f"تمت إضافة {symbol} بنسبة {new_pct}%")
        return {"actions": actions, "errors": errors, "success": len(errors) == 0}

    def remove_asset(self, portfolio: Portfolio, symbol: str, sell: bool = True) -> dict:
        """Remove an asset from portfolio (optionally sell it)."""
        actions, errors = [], []
        exch = getattr(portfolio, "exchange", None) or getattr(portfolio.config, "exchange", "bitget")
        if exch != self.exchange:
            self.set_exchange(exch)

        symbol = symbol.upper()
        asset = None
        for a in portfolio.assets:
            if a.symbol == symbol and a.status == "active":
                asset = a
                break

        if not asset:
            return {"actions": [], "errors": [f"{symbol} غير موجودة أو غير نشطة"], "success": False}

        if sell:
            try:
                self.client.close_all_at_market([symbol])
                actions.append(f"تم بيع {symbol}")
            except Exception as e:
                errors.append(f"فشل البيع: {e}")

        asset.status = "closed"
        try:
            db.update_asset(asset.id, {"status": "closed"})
        except Exception as e:
            errors.append(f"فشل تحديث حالة الأصل: {e}")

        # Redistribute target_pct among remaining
        active = [a for a in portfolio.assets if a.status == "active"]
        if active:
            equal = round(100.0 / len(active), 2)
            for a in active:
                a.target_pct = equal
                try:
                    db.update_asset(a.id, {"target_pct": equal})
                except Exception:
                    pass

        actions.append(f"تم حذف {symbol} من المحفظة")
        return {"actions": actions, "errors": errors, "success": len(errors) == 0}

    def add_funds(self, portfolio: Portfolio, usdt_amount: float) -> dict:
        """Add more USDT to the portfolio and buy proportionally."""
        actions, errors = [], []
        exch = getattr(portfolio, "exchange", None) or getattr(portfolio.config, "exchange", "bitget")
        if exch != self.exchange:
            self.set_exchange(exch)

        active = [a for a in portfolio.assets if a.status == "active"]
        if not active:
            return {"actions": [], "errors": ["لا توجد عملات نشطة"], "success": False}

        total_pct = sum(a.target_pct for a in active) or 100.0

        for a in active:
            portion = usdt_amount * (a.target_pct / total_pct)
            if portion < 5:
                continue
            try:
                self.client.place_market_order(a.symbol, "buy", portion)
                actions.append(f"شراء إضافي {a.symbol}: {portion:.2f} USDT")
            except Exception as e:
                errors.append(f"فشل شراء {a.symbol}: {e}")

        portfolio.config.total_investment = float(portfolio.config.total_investment or 0) + usdt_amount
        try:
            db.update_portfolio(portfolio.id, {"total_investment": portfolio.config.total_investment})
        except Exception as e:
            errors.append(f"فشل تحديث مبلغ الاستثمار: {e}")

        actions.append(f"تمت زيادة الاستثمار بـ {usdt_amount:.2f} USDT")
        return {"actions": actions, "errors": errors, "success": len(errors) == 0}

    def reduce_funds(self, portfolio: Portfolio, usdt_amount: float) -> dict:
        """Sell proportionally to reduce portfolio size."""
        actions, errors = [], []
        exch = getattr(portfolio, "exchange", None) or getattr(portfolio.config, "exchange", "bitget")
        if exch != self.exchange:
            self.set_exchange(exch)

        active = [a for a in portfolio.assets if a.status == "active"]
        if not active:
            return {"actions": [], "errors": ["لا توجد عملات نشطة"], "success": False}

        snap = self.snapshot(portfolio)
        total_value = snap.get("total_value") or 0
        if total_value <= 0:
            return {"actions": [], "errors": ["قيمة المحفظة صفر"], "success": False}

        ratio = min(usdt_amount / total_value, 0.95)  # max 95%

        for item in snap.get("assets", []):
            sym = item.get("symbol")
            val = item.get("value") or 0
            sell_usdt = val * ratio
            if sell_usdt < 5:
                continue
            try:
                self.client.place_market_order(sym, "sell", sell_usdt)
                actions.append(f"بيع جزئي {sym}: {sell_usdt:.2f} USDT")
            except Exception as e:
                errors.append(f"فشل بيع {sym}: {e}")

        portfolio.config.total_investment = max(0, float(portfolio.config.total_investment or 0) - usdt_amount)
        try:
            db.update_portfolio(portfolio.id, {"total_investment": portfolio.config.total_investment})
        except Exception as e:
            errors.append(f"فشل تحديث مبلغ الاستثمار: {e}")

        actions.append(f"تم تخفيف الاستثمار بـ {usdt_amount:.2f} USDT")
        return {"actions": actions, "errors": errors, "success": len(errors) == 0}


    # ── Close ─────────────────────────────────────────────────────────────────

    def close_portfolio(self, portfolio: Portfolio, sell: bool = True) -> float:
        exch = getattr(portfolio, "exchange", None) or portfolio.config.exchange or "bitget"
        if exch != self.exchange:
            self.set_exchange(exch)

        symbols = [a.symbol for a in portfolio.assets if a.status == "active"]
        if sell and symbols:
            try:
                self.client.close_all_at_market(symbols)
            except Exception as e:
                logger.error("close_all_at_market failed: %s", e)

        snap = self.snapshot(portfolio)
        final_value = snap["total_value"]
        for a in portfolio.assets:
            a.status = "closed"
        portfolio.status = "closed"
        db.close_portfolio(portfolio.id, final_value)
        return final_value

    # ── Load ──────────────────────────────────────────────────────────────────

    def load_portfolio(self, portfolio_id: str) -> Optional[Portfolio]:
        row = db.get_portfolio(portfolio_id)
        if not row:
            return None
        # db.py uses get_portfolio_assets (not get_assets)
        assets_rows = db.get_portfolio_assets(portfolio_id)
        exchange = row.get("exchange") or "bitget"
        config = PortfolioConfig(
            total_investment=float(row.get("total_investment") or 0),
            assets=[AssetConfig(a["symbol"], float(a["target_pct"])) for a in assets_rows if a.get("status") == "active"],
            rebalance_mode=row.get("rebalance_mode") or "time",
            interval_hours=float(row.get("interval_hours") or 0),
            threshold_pct=float(row.get("threshold_pct") or 0),
            exchange=exchange,
        )
        portfolio = Portfolio(
            id=portfolio_id,
            config=config,
            exchange=exchange,
            status=row.get("status") or "active",
            total_pnl=float(row.get("total_pnl") or 0),
        )
        if row.get("last_rebalance_at"):
            try:
                portfolio.last_rebalance_at = datetime.fromisoformat(
                    str(row["last_rebalance_at"]).replace("Z", "+00:00")
                )
            except Exception:
                pass
        for a in assets_rows:
            portfolio.assets.append(PortfolioAsset(
                id=a["id"],
                symbol=a["symbol"],
                target_pct=float(a["target_pct"]),
                initial_qty=float(a.get("initial_qty") or 0),
                status=a.get("status") or "active",
            ))
        self._portfolios[portfolio_id] = portfolio
        return portfolio


_engines: dict[str, RebalanceEngine] = {}


def get_engine(exchange: str = "bitget") -> RebalanceEngine:
    exchange = (exchange or "bitget").lower()
    if exchange not in _engines:
        _engines[exchange] = RebalanceEngine(exchange)
    return _engines[exchange]
