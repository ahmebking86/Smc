"""
Portfolio Rebalancing Engine.

Creates a portfolio of up to 20 coins with target allocation percentages,
then periodically (by time or by deviation %) rebalances by market buy/sell.
Uses the existing BitGet client methods to avoid precision / min-notional errors.
"""

from __future__ import annotations
import uuid
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from trading.bitget_client import get_bitget
from database import db
from config import MIN_ORDER_USDT, MAX_ASSETS

logger = logging.getLogger(__name__)


@dataclass
class AssetConfig:
    symbol: str
    target_pct: float          # 0–100


@dataclass
class PortfolioConfig:
    total_investment: float    # total USDT
    assets: list[AssetConfig]
    rebalance_mode: str        # "time" | "percent"
    interval_hours: float = 0  # for time mode
    threshold_pct: float = 0   # for percent mode (deviation that triggers)


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


class RebalanceEngine:

    def __init__(self):
        self.client = get_bitget()
        self._portfolios: dict[str, Portfolio] = {}

    # ── Create ────────────────────────────────────────────────────────────────

    def create_portfolio(self, config: PortfolioConfig) -> Portfolio:
        if not self.client.has_credentials():
            raise RuntimeError(
                "مفاتيح BitGet API غير مُعيَّنة.\n"
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
        })

        portfolio = Portfolio(
            id=portfolio_id,
            config=config,
            last_rebalance_at=now,
            created_at=now,
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
                est_qty = round(usdt / price * 0.998, qty_places)
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
                time.sleep(0.3)
            except Exception as e:
                logger.error("Initial buy %s failed: %s", ac.symbol, e)
                errors.append(f"❌ {ac.symbol}: {e}")

        if not portfolio.assets:
            db.close_portfolio(portfolio_id, 0)
            raise RuntimeError(
                "تعذّر شراء أي عملة.\n" + "\n".join(errors)
            )

        self._portfolios[portfolio_id] = portfolio
        portfolio._create_results = results
        portfolio._create_errors = errors
        logger.info(
            "Portfolio %s created | %d assets | invest=%.2f",
            portfolio_id[:8], len(portfolio.assets), config.total_investment,
        )
        return portfolio

    # ── Snapshot helpers ──────────────────────────────────────────────────────

    def _get_balances(self) -> dict[str, float]:
        balances: dict[str, float] = {}
        try:
            raw = self.client.get_account_balance()
            for item in raw:
                coin = item.get("coin") or item.get("currency") or item.get("asset") or ""
                avail = float(
                    item.get("available")
                    or item.get("availableBalance")
                    or item.get("free")
                    or 0
                )
                if coin and avail > 0:
                    balances[coin.upper()] = avail
        except Exception as e:
            logger.error("get_account_balance failed: %s", e)
        return balances

    def snapshot(self, portfolio: Portfolio) -> dict:
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

        for a in snap["assets"]:
            excess_pct = a["deviation"]
            if excess_pct <= 0.1:
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
                actions.append(f"🔴 بيع {a['coin']}: {sell_qty} ≈ {sell_qty*price:.2f} USDT (انحراف +{excess_pct:.1f}%)")
                time.sleep(0.4)
            except Exception as e:
                logger.error("Rebalance sell %s: %s", a["symbol"], e)
                errors.append(f"بيع {a['coin']}: {e}")

        if actions:
            time.sleep(1.5)

        balances = self._get_balances()
        usdt_available = balances.get("USDT", 0.0)

        for a in snap["assets"]:
            deficit_pct = -a["deviation"]
            if deficit_pct <= 0.1:
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
                est_qty = round(need_usdt / price * 0.998, qty_places)
                db.create_trade({
                    "id": str(uuid.uuid4()),
                    "portfolio_id": portfolio.id,
                    "symbol": a["symbol"],
                    "side": "buy",
                    "usdt_amount": need_usdt,
                    "qty": est_qty,
                    "price": price,
                })
                actions.append(f"🟢 شراء {a['coin']}: ≈ {need_usdt:.2f} USDT (انحراف -{deficit_pct:.1f}%)")
                usdt_available -= need_usdt
                time.sleep(0.4)
            except Exception as e:
                logger.error("Rebalance buy %s: %s", a["symbol"], e)
                errors.append(f"شراء {a['coin']}: {e}")

        now = datetime.now(timezone.utc)
        portfolio.last_rebalance_at = now
        db.update_portfolio(portfolio.id, {"last_rebalance_at": now.isoformat()})

        return {
            "actions": actions,
            "errors": errors,
            "total_value": total,
        }

    # ── Close ─────────────────────────────────────────────────────────────────

    def close_portfolio(self, portfolio: Portfolio, sell: bool = True) -> float:
        symbols = [a.symbol for a in portfolio.assets if a.status == "active"]
        if sell and symbols:
            try:
                self.client.close_all_at_market(symbols)
            except Exception as e:
                logger.error("close_all_at_market failed: %s", e)
                balances = self._get_balances()
                for sym in symbols:
                    coin = sym.replace("USDT", "")
                    qty = balances.get(coin, 0)
                    if qty <= 0:
                        continue
                    try:
                        _, qp = self.client.get_symbol_precision(sym)
                        self.client.place_market_sell(sym, qty, qp)
                    except Exception as e2:
                        logger.warning("Fallback sell %s: %s", sym, e2)

        pnl = db.portfolio_total_pnl(portfolio.id)
        db.close_portfolio(portfolio.id, pnl)
        portfolio.status = "closed"
        portfolio.total_pnl = pnl
        self._portfolios.pop(portfolio.id, None)
        return pnl

    def close_asset(self, portfolio: Portfolio, symbol: str) -> str:
        asset = next((a for a in portfolio.assets if a.symbol == symbol and a.status == "active"), None)
        if not asset:
            return f"العملة {symbol} غير موجودة في المحفظة."
        coin = symbol.replace("USDT", "")
        balances = self._get_balances()
        qty = balances.get(coin, 0)
        msg = ""
        if qty > 0:
            try:
                _, qp = self.client.get_symbol_precision(symbol)
                price = self.client.get_price(symbol)
                if qty * price >= MIN_ORDER_USDT:
                    self.client.place_market_sell(symbol, qty, qp)
                    db.create_trade({
                        "id": str(uuid.uuid4()),
                        "portfolio_id": portfolio.id,
                        "symbol": symbol,
                        "side": "sell",
                        "usdt_amount": round(qty * price, 2),
                        "qty": qty,
                        "price": price,
                    })
                    msg = f"✅ تم بيع {coin}: {qty} ≈ {qty*price:.2f} USDT"
                else:
                    msg = f"⚠️ الكمية صغيرة جداً ({qty}) — تم التخطي"
            except Exception as e:
                msg = f"❌ فشل البيع: {e}"
        else:
            msg = f"لا يوجد رصيد لـ {coin}"

        db.deactivate_asset(asset.id)
        asset.status = "closed"
        return msg

    def replace_asset(self, portfolio: Portfolio, old_symbol: str, new_symbol: str) -> dict:
        """استبدال عملة: بيع القديمة وشراء الجديدة بنفس القيمة تقريباً."""
        old_symbol = old_symbol.upper()
        new_symbol = new_symbol.upper()
        if not new_symbol.endswith("USDT"):
            new_symbol += "USDT"

        old_asset = None
        for a in portfolio.assets:
            if a.symbol == old_symbol and a.status == "active":
                old_asset = a
                break
        if not old_asset:
            raise RuntimeError(f"العملة {old_symbol} غير موجودة في المحفظة")

        for a in portfolio.assets:
            if a.symbol == new_symbol and a.status == "active":
                raise RuntimeError(f"العملة {new_symbol} موجودة بالفعل في المحفظة")

        try:
            new_price = self.client.get_price(new_symbol)
        except Exception as e:
            raise RuntimeError(f"تعذر جلب سعر {new_symbol}: {e}")

        snap = self.snapshot(portfolio)
        old_row = next((r for r in snap["assets"] if r["symbol"] == old_symbol), None)
        if not old_row or old_row["qty"] <= 0:
            raise RuntimeError(f"لا يوجد رصيد لـ {old_symbol}")

        usdt_value = old_row["value"]
        if usdt_value < MIN_ORDER_USDT:
            raise RuntimeError(f"قيمة {old_symbol} صغيرة جداً ({usdt_value:.2f} USDT)")

        actions = []
        errors = []

        try:
            _, qty_places = self.client.get_symbol_precision(old_symbol)
            sell_qty = old_row["qty"]
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
            time.sleep(1.2)
        except Exception as e:
            raise RuntimeError(f"فشل بيع {old_symbol}: {e}")

        try:
            buy_usdt = round(usdt_value * 0.997, 2)
            if buy_usdt < MIN_ORDER_USDT:
                buy_usdt = MIN_ORDER_USDT
            self.client.place_market_buy_usdt(new_symbol, buy_usdt)
            _, qty_places = self.client.get_symbol_precision(new_symbol)
            est_qty = round(buy_usdt / new_price * 0.998, qty_places)
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
            return {"actions": actions, "errors": errors, "success": False}

        db.deactivate_asset(old_asset.id)
        old_asset.status = "closed"

        new_asset_id = str(uuid.uuid4())
        db.create_asset({
            "id": new_asset_id,
            "portfolio_id": portfolio.id,
            "symbol": new_symbol,
            "target_pct": old_asset.target_pct,
            "initial_qty": est_qty,
            "status": "active",
        })
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
            "success": True,
            "old_symbol": old_symbol,
            "new_symbol": new_symbol,
            "usdt_value": usdt_value,
        }


    # ── حذف عملة ──────────────────────────────────────────────────────────────

    def remove_asset(self, portfolio: "Portfolio", symbol: str, sell: bool = True) -> dict:
        """يحذف عملة من المحفظة (يبيعها ويعيد توزيع النسب)."""
        asset = next((a for a in portfolio.assets if a.symbol == symbol and a.status == "active"), None)
        if not asset:
            return {"ok": False, "msg": f"العملة {symbol} غير موجودة أو غير نشطة"}

        result = {"ok": True, "actions": [], "errors": []}

        if sell:
            try:
                balances = self._get_balances()
                coin = symbol.replace("USDT", "")
                qty = balances.get(coin, 0.0)
                if qty > 0:
                    price = self.client.get_price(symbol)
                    _, qty_places = self.client.get_symbol_precision(symbol)
                    sell_qty = round(qty, qty_places)
                    if sell_qty * price >= MIN_ORDER_USDT:
                        self.client.place_market_sell(symbol, sell_qty, qty_places)
                        result["actions"].append(f"🔴 تم بيع {coin}: {sell_qty}")
            except Exception as e:
                result["errors"].append(str(e))

        remaining = [a for a in portfolio.assets if a.symbol != symbol and a.status == "active"]
        if not remaining:
            return {"ok": False, "msg": "لا يمكن حذف آخر عملة في المحفظة"}

        total_pct = sum(a.target_pct for a in remaining)
        for a in remaining:
            a.target_pct = round(a.target_pct / total_pct * 100, 2)

        asset.status = "closed"
        db.deactivate_asset(asset.id)
        result["actions"].append(f"✅ تم حذف {symbol.replace('USDT', '')} وإعادة توزيع النسب")
        return result

    # ── زيادة الاستثمار ────────────────────────────────────────────────────────

    def add_funds(self, portfolio: "Portfolio", amount: float) -> dict:
        """يوزع مبلغ جديد حسب النسب الحالية."""
        if amount < MIN_ORDER_USDT:
            return {"ok": False, "msg": f"المبلغ أقل من الحد الأدنى {MIN_ORDER_USDT}"}

        actions, errors = [], []
        for asset in portfolio.assets:
            if asset.status != "active":
                continue
            usdt = round(amount * asset.target_pct / 100.0, 2)
            if usdt < MIN_ORDER_USDT:
                continue
            try:
                self.client.place_market_buy_usdt(asset.symbol, usdt)
                actions.append(f"🟢 {asset.symbol}: +{usdt:.2f} USDT")
                time.sleep(0.3)
            except Exception as e:
                errors.append(f"{asset.symbol}: {e}")

        portfolio.config.total_investment += amount
        db.update_portfolio(portfolio.id, {"total_investment": portfolio.config.total_investment})
        return {"ok": True, "actions": actions, "errors": errors}

    # ── تخفيف الاستثمار ────────────────────────────────────────────────────────

    def reduce_funds(self, portfolio: "Portfolio", percent: float) -> dict:
        """يبيع نسبة مئوية من كل عملة."""
        if percent <= 0 or percent > 100:
            return {"ok": False, "msg": "النسبة يجب أن تكون بين 1 و 100"}

        snap = self.snapshot(portfolio)
        actions, errors = [], []

        for a in snap["assets"]:
            sell_value = a["value"] * (percent / 100.0)
            if sell_value < MIN_ORDER_USDT:
                continue
            try:
                price = a["price"]
                _, qty_places = self.client.get_symbol_precision(a["symbol"])
                sell_qty = math.floor((sell_value / price) * 10**qty_places) / 10**qty_places
                sell_qty = min(sell_qty, a["qty"])
                if sell_qty * price < MIN_ORDER_USDT:
                    continue
                self.client.place_market_sell(a["symbol"], sell_qty, qty_places)
                actions.append(f"🔴 {a['coin']}: بيع ≈ {sell_value:.2f} USDT")
                time.sleep(0.3)
            except Exception as e:
                errors.append(f"{a['coin']}: {e}")

        portfolio.config.total_investment *= (1 - percent / 100)
        db.update_portfolio(portfolio.id, {"total_investment": portfolio.config.total_investment})
        return {"ok": True, "actions": actions, "errors": errors}

    # ── تقرير الأداء ───────────────────────────────────────────────────────────

    def performance_report(self, portfolio: "Portfolio") -> str:
        """يرجع نص تقرير مرتب حسب الأداء."""
        snap = self.snapshot(portfolio)
        lines = ["📈 <b>تقرير أداء المحفظة</b>\n"]
        ranked = []

        for a in snap["assets"]:
            ranked.append((a["coin"], a["value"], a["current_pct"], a["deviation"]))

        ranked.sort(key=lambda x: x[2], reverse=True)

        for i, (coin, value, pct, dev) in enumerate(ranked, 1):
            emoji = "🟢" if dev >= 0 else "🔴"
            lines.append(f"{i}. {emoji} <b>{coin}</b> — {value:.2f} USDT ({pct:.1f}%) انحراف {dev:+.1f}%")

        lines.append(f"\n💰 القيمة الإجمالية: <b>{snap['total_value']:.2f} USDT</b>")
        return "\n".join(lines)

    # ── Load / manage ─────────────────────────────────────────────────────────

    def load_from_db(self) -> None:
        rows = db.list_active_portfolios()
        for row in rows:
            assets_rows = db.get_portfolio_assets(row["id"])
            assets = [
                PortfolioAsset(
                    id=a["id"],
                    symbol=a["symbol"],
                    target_pct=float(a["target_pct"]),
                    initial_qty=float(a.get("initial_qty") or 0),
                    status=a.get("status", "active"),
                )
                for a in assets_rows
            ]
            cfg = PortfolioConfig(
                total_investment=float(row["total_investment"]),
                assets=[AssetConfig(a.symbol, a.target_pct) for a in assets],
                rebalance_mode=row["rebalance_mode"],
                interval_hours=float(row.get("interval_hours") or 0),
                threshold_pct=float(row.get("threshold_pct") or 0),
            )
            last_rb = row.get("last_rebalance_at")
            if isinstance(last_rb, str):
                try:
                    last_rb = datetime.fromisoformat(last_rb.replace("Z", "+00:00"))
                except Exception:
                    last_rb = None
            p = Portfolio(
                id=row["id"],
                config=cfg,
                assets=assets,
                total_pnl=float(row.get("total_pnl") or 0),
                status=row.get("status", "active"),
                last_rebalance_at=last_rb,
            )
            self._portfolios[p.id] = p
        logger.info("Loaded %d portfolios from DB", len(self._portfolios))

    def get_portfolio(self, pid: str) -> Optional[Portfolio]:
        return self._portfolios.get(pid)

    def all_active(self) -> list[Portfolio]:
        return [p for p in self._portfolios.values() if p.status == "active"]

    def all_portfolios(self) -> list[Portfolio]:
        return list(self._portfolios.values())


_engine: Optional[RebalanceEngine] = None


def get_engine() -> RebalanceEngine:
    global _engine
    if _engine is None:
        _engine = RebalanceEngine()
    return _engine
