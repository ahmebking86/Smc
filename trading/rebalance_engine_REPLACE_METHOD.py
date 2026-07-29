# ═══════════════════════════════════════════════════════════════════════════════
# الصق الدالة دي داخل كلاس RebalanceEngine
# بعد دالة close_asset وقبل قسم Load / manage
# ═══════════════════════════════════════════════════════════════════════════════

    def replace_asset(self, portfolio: Portfolio, old_symbol: str, new_symbol: str) -> dict:
        """
        استبدال عملة: بيع القديمة وشراء الجديدة بنفس القيمة تقريباً.
        يحافظ على نفس target_pct.
        """
        old_symbol = old_symbol.upper()
        new_symbol = new_symbol.upper()
        if not new_symbol.endswith("USDT"):
            new_symbol += "USDT"

        # لقى الأصل القديم
        old_asset = None
        for a in portfolio.assets:
            if a.symbol == old_symbol and a.status == "active":
                old_asset = a
                break
        if not old_asset:
            raise RuntimeError(f"العملة {old_symbol} غير موجودة في المحفظة")

        # تأكد إن الجديدة مش موجودة أصلاً
        for a in portfolio.assets:
            if a.symbol == new_symbol and a.status == "active":
                raise RuntimeError(f"العملة {new_symbol} موجودة بالفعل في المحفظة")

        # تحقق من السعر
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

        # 1. بيع القديمة
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

        # 2. شراء الجديدة
        try:
            buy_usdt = round(usdt_value * 0.997, 2)  # خصم رسوم تقريبي
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

        # 3. تحديث قاعدة البيانات والذاكرة
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
