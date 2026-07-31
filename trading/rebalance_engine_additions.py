"""
دوال جديدة تُضاف داخل class RebalanceEngine في trading/rebalance_engine.py
"""

# ── حذف عملة ────────────────────────────────────────────────────────────────

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

    # إزالة العملة وإعادة توزيع النسب
    remaining = [a for a in portfolio.assets if a.symbol != symbol and a.status == "active"]
    if not remaining:
        return {"ok": False, "msg": "لا يمكن حذف آخر عملة في المحفظة"}

    total_pct = sum(a.target_pct for a in remaining)
    for a in remaining:
        a.target_pct = round(a.target_pct / total_pct * 100, 2)

    asset.status = "closed"
    # هنا تحتاج تحديث في الداتابيز (db.update_asset + db.update_portfolio)
    result["actions"].append(f"✅ تم حذف {symbol.replace('USDT','')} وإعادة توزيع النسب")
    return result


# ── زيادة الاستثمار ──────────────────────────────────────────────────────────

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
    # db.update_portfolio ...
    return {"ok": True, "actions": actions, "errors": errors}


# ── تخفيف الاستثمار ─────────────────────────────────────────────────────────

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

    # تحديث total_investment تقريبي
    portfolio.config.total_investment *= (1 - percent / 100)
    return {"ok": True, "actions": actions, "errors": errors}


# ── تقرير الأداء ─────────────────────────────────────────────────────────────

def performance_report(self, portfolio: "Portfolio") -> str:
    """يرجع نص تقرير مرتب حسب الأداء."""
    snap = self.snapshot(portfolio)
    lines = ["📈 <b>تقرير أداء المحفظة</b>\n"]
    ranked = []

    for a in snap["assets"]:
        # تقريب بسيط: نحتاج initial value من الداتابيز
        # هنا نستخدم قيمة تقريبية
        ranked.append((a["coin"], a["value"], a["current_pct"], a["deviation"]))

    ranked.sort(key=lambda x: x[2], reverse=True)  # حسب النسبة الحالية

    for i, (coin, value, pct, dev) in enumerate(ranked, 1):
        emoji = "🟢" if dev >= 0 else "🔴"
        lines.append(f"{i}. {emoji} <b>{coin}</b> — {value:.2f} USDT ({pct:.1f}%) انحراف {dev:+.1f}%")

    lines.append(f"\n💰 القيمة الإجمالية: <b>{snap['total_value']:.2f} USDT</b>")
    return "\n".join(lines)
