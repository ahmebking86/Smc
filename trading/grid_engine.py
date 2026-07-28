"""
Core grid trading engine.
"""

from __future__ import annotations
import uuid
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# ── BitGet minimum order notional ─────────────────────────────────────────────
BITGET_MIN_NOTIONAL = 1.0   # USDT — reject any order whose price×qty < this

from trading.bitget_client import get_bitget
from database import db

logger = logging.getLogger(__name__)


@dataclass
class GridConfig:
    symbol:        str
    entry_amount:  float          # USDT per level (infinite) or total budget (legacy)
    # ── Infinite-grid fields (step_pct > 0 activates this mode) ──────────────
    step_pct:          float = 0.0  # % gap between adjacent levels (نسبة ربح كل حركة)
    levels_per_side:   int   = 5    # initial levels above + below current price
    lower_limit_price: float = 0.0  # hard floor: no buy orders placed below this
    upper_limit_price: float = 0.0  # hard ceiling: no sell orders placed above this
    # ── Legacy fixed-range fields (used when step_pct == 0) ──────────────────
    upper_pct:     float = 0.0
    lower_pct:     float = 0.0
    grid_count:    int   = 10
    # ── Common fields ─────────────────────────────────────────────────────────
    profit_target: float = 0.0   # 0 = disabled
    stop_loss:     float = 0.0   # 0 = disabled  (global per-coin %)
    depth:          int   = 0
    parent_id:      Optional[str] = None
    group_id:       Optional[str] = None  # UUID shared by sessions created together
    trailing_stop:  bool  = False
    trailing_pct:   float = 0.0


@dataclass
class GridLevel:
    price:       float
    qty:         float
    side:        str
    order_id:    Optional[str]   = None
    db_id:       Optional[str]   = None
    status:      str             = "open"
    # FIX: store the buy price that triggered a counter-sell, so P&L is only
    # recorded when the sell *actually fills* (not when it's placed).
    entry_price: Optional[float] = None


@dataclass
class GridSession:
    id:          str
    config:      GridConfig
    base_price:  float
    upper_price: float
    lower_price: float
    levels:      list[GridLevel] = field(default_factory=list)
    total_pnl:   float = 0.0
    status:      str = "active"


class GridEngine:

    def __init__(self):
        self.client = get_bitget()
        self._sessions: dict[str, GridSession] = {}

    def create_session(self, config: GridConfig) -> GridSession:
        if not self.client.has_credentials():
            raise RuntimeError(
                "مفاتيح BitGet API غير مُعيَّنة.\n"
                "اضغط 🔑 إعداد API من القائمة الرئيسية."
            )

        # Fetch price and symbol precision in one pass
        price = self.client.get_price(config.symbol)
        price_places, qty_places = self.client.get_symbol_precision(config.symbol)

        infinite = config.step_pct > 0

        if infinite:
            # BingX Infinity Grid is Arithmetic (fixed price step, not geometric %)
            # We use step_pct of current price to determine the fixed price step.
            step = round(price * config.step_pct / 100, price_places)
            
            # If a hard limit price is provided, derive levels_per_side from it
            if config.lower_limit_price > 0 and config.lower_limit_price < price:
                config.levels_per_side = max(1, round(
                    (price - config.lower_limit_price) / step
                ))
            elif config.upper_limit_price > 0 and config.upper_limit_price > price:
                config.levels_per_side = max(1, round(
                    (config.upper_limit_price - price) / step
                ))
            
            # Infinity Grid maintains total USDT value. 
            # Initial range for order placement:
            upper = round(price + config.levels_per_side * step, price_places)
            lower = round(price - config.levels_per_side * step, price_places)
        else:
            upper = round(price * (1 + config.upper_pct / 100), price_places)
            lower = round(price * (1 - config.lower_pct / 100), price_places)
            if upper <= lower:
                raise RuntimeError(
                    f"⚠️ نطاق الشبكة صفر بعد التقريب.\n\n"
                    f"السعر الحالي: <code>{price}</code> — دقة BitGet: {price_places} خانة عشرية\n"
                    f"النطاق الأعلى ({config.upper_pct}%) والأدنى ({config.lower_pct}%) يتطابقان بعد التقريب.\n\n"
                    f"💡 الحل: اختر عملة ذات سعر أعلى، أو زد النسبة المئوية للنطاق."
                )

        session_id = str(uuid.uuid4())

        db.create_session({
            "id":                session_id,
            "symbol":            config.symbol,
            "entry_amount":      config.entry_amount,
            "upper_pct":         config.upper_pct,
            "lower_pct":         config.lower_pct,
            "grid_count":        config.grid_count,
            "step_pct":          config.step_pct,
            "levels_per_side":   config.levels_per_side,
            "lower_limit_price": config.lower_limit_price,
            "upper_limit_price": config.upper_limit_price,
            "profit_target":     config.profit_target,
            "stop_loss":         config.stop_loss,
            "base_price":        price,
            "upper_price":       upper,
            "lower_price":       lower,
            "status":            "active",
            "total_pnl":         0.0,
            "trailing_stop":     config.trailing_stop,
            "trailing_pct":      config.trailing_pct,
            "depth":             config.depth,
            "parent_id":         config.parent_id,
            "group_id":          config.group_id,
        })

        session = GridSession(
            id=session_id,
            config=config,
            base_price=price,
            upper_price=upper,
            lower_price=lower,
        )

        # ── FIX: pre-flight minimum-notional check ────────────────────────────
        # Calculate how much USDT each individual limit order will be worth.
        # BitGet rejects any order whose price × qty < 1 USDT (code 45110).
        # We warn the user early and clearly instead of spamming 10+ failed orders.
        if infinite:
            amt_per_lvl = config.entry_amount / max(config.levels_per_side, 1)
            if amt_per_lvl < BITGET_MIN_NOTIONAL:
                logger.warning(
                    "⚠️ amount_per_level=%.4f < %.1f USDT — bumping to minimum for all orders",
                    amt_per_lvl, BITGET_MIN_NOTIONAL,
                )
                # We don't raise here — _build_levels will bump qty to meet the
                # minimum automatically. We just log so the admin knows.

        levels = self._build_levels(session, price_places, qty_places)

        # Market buy base asset to fund initial sell orders above current price
        sell_levels_init = [l for l in levels if l.side == "sell"]
        if sell_levels_init:
            total_sell_qty = round(sum(l.qty for l in sell_levels_init), qty_places)
            # FIX: BitGet market-buy `size` = USDT amount, not token quantity.
            # Multiply tokens × current price to get the correct USDT value.
            # FIX: Added 2% buffer to cover fees and price slippage during market buy
            # This ensures we have enough tokens to place all initial sell orders.
            usdt_needed = round(total_sell_qty * price * 1.02, 2)
            try:
                self.client.place_market_buy_usdt(config.symbol, usdt_needed)
                logger.info(
                    "Market buy %.2f USDT (≈ %.6f tokens) for %d initial sell levels",
                    usdt_needed, total_sell_qty, len(sell_levels_init),
                )
            except Exception as e:
                logger.warning(
                    "Market buy for initial sell levels failed: %s — sell levels removed", e
                )
                levels = [l for l in levels if l.side == "buy"]

        session.levels = levels
        placed, failed, first_error = self._place_orders(session, price_places, qty_places)

        self._sessions[session_id] = session
        logger.info(
            "Session %s | %s @ %s [%s–%s] placed=%d failed=%d",
            session_id[:8], config.symbol,
            f"{price:.{price_places}f}",
            f"{lower:.{price_places}f}",
            f"{upper:.{price_places}f}",
            placed, failed,
        )

        # FIX: placed==0 AND failed==0 يعني كل مستويات الشبكة فوق السعر الحالي
        # (لا توجد مستويات شراء أصلاً). الكود القديم كان يتجاهل هذه الحالة
        # ويُنشئ جلسة بلا أوامر تُزعج المستخدم بتنبيهات "خرج عن النطاق".
        if placed == 0 and failed == 0:
            db.close_session(session_id, 0)
            session.status = "closed"
            self._sessions.pop(session_id, None)
            raise RuntimeError(
                "⚠️ لا توجد مستويات شراء تحت السعر الحالي.\n\n"
                f"السعر الحالي: <code>{price:.{price_places}f}</code>\n"
                "جميع نقاط الشبكة وقعت فوق السعر.\n\n"
                "💡 الحل: زد نسبة النطاق الأدنى أو قلّل عدد الشبكات."
            )

        if placed == 0 and failed > 0:
            db.close_session(session_id, 0)
            session.status = "closed"
            self._sessions.pop(session_id, None)

            err_detail = ""
            if first_error:
                err_detail = f"\n\n<b>خطأ BitGet:</b> <code>{first_error}</code>"
                if "41103" in first_error or "scale" in first_error.lower():
                    err_detail += "\n💡 خطأ في دقة السعر — تم إصلاحه، حاول مجدداً."
                elif "45110" in first_error or "minimum" in first_error.lower():
                    err_detail += "\n💡 المبلغ أقل من الحد الأدنى — زد مبلغ الدخول."

            raise RuntimeError(
                f"تعذّر إرسال أي أمر لـ BitGet ({failed} محاولة فاشلة).{err_detail}"
            )

        session._placed_count = placed
        session._failed_count = failed
        return session

    def _build_levels(
        self, session: GridSession,
        price_places: int = 2, qty_places: int = 6,
    ) -> list[GridLevel]:
        cfg = session.config

        if cfg.step_pct > 0:
            # ── Infinity-grid mode (Arithmetic) ───────────────────────────────
            # BingX Spec: Maintain constant USDT value. 
            # If BTC is 20k and profit is 1%, when it hits 20.2k, sell 200 USDT of BTC.
            # This means each grid level represents a fixed USDT amount (profit).
            
            step = round(session.base_price * cfg.step_pct / 100, price_places)
            
            # entry_amount in this mode is interpreted as "Amount per Level" (USDT)
            # to match the BingX behavior where each grid fill realizes this amount.
            amount_per_level = cfg.entry_amount
            effective_amount = max(amount_per_level, BITGET_MIN_NOTIONAL)
            
            levels: list[GridLevel] = []
            for i in range(1, cfg.levels_per_side + 1):
                # Buy level below
                buy_price = round(session.base_price - i * step, price_places)
                if buy_price > 0:
                    if cfg.lower_limit_price > 0 and buy_price < cfg.lower_limit_price:
                        pass
                    else:
                        # Qty needed to have 'effective_amount' USDT value at this price
                        raw_qty = effective_amount / buy_price
                        qty = math.ceil(raw_qty * 10**qty_places) / 10**qty_places
                        qty = round(qty, qty_places)
                        if qty > 0:
                            levels.append(GridLevel(price=buy_price, qty=qty, side="buy"))
                
                # Sell level above
                sell_price = round(session.base_price + i * step, price_places)
                if cfg.upper_limit_price > 0 and sell_price > cfg.upper_limit_price:
                    pass
                else:
                    if sell_price > 0:
                        raw_qty = effective_amount / sell_price
                        qty = math.ceil(raw_qty * 10**qty_places) / 10**qty_places
                        qty = round(qty, qty_places)
                        if qty > 0:
                            levels.append(GridLevel(price=sell_price, qty=qty, side="sell"))
            return levels

        # ── Legacy fixed-range mode ───────────────────────────────────────────
        n   = cfg.grid_count
        if n <= 0:
            raise RuntimeError(
                f"⚠️ grid_count={n} غير صالح للشبكة {session.id[:8]} ({cfg.symbol}).\n"
                "لا يمكن بناء مستويات بعدد شبكات صفر."
            )
        step = (session.upper_price - session.lower_price) / n

        grid_prices = [
            round(session.lower_price + i * step, price_places)
            for i in range(n + 1)
        ]

        amount_per_grid = cfg.entry_amount / (n + 1)
        # FIX: enforce BitGet minimum 1 USDT notional on legacy grid orders
        effective_grid_amt = max(amount_per_grid, BITGET_MIN_NOTIONAL)
        levels = []

        for gp in grid_prices:
            if gp <= 0:
                continue
            if gp < session.base_price:
                raw_qty = effective_grid_amt / gp
                qty = math.ceil(raw_qty * 10**qty_places) / 10**qty_places
                qty = round(qty, qty_places)
                if qty <= 0:
                    logger.warning(
                        "_build_levels: qty=0 at price=%s "
                        "(amount_per_grid=%s qty_places=%d) — skipped",
                        gp, amount_per_grid, qty_places,
                    )
                    continue
                levels.append(GridLevel(price=gp, qty=qty, side="buy"))
            elif gp > session.base_price:
                raw_qty = effective_grid_amt / gp
                qty = math.ceil(raw_qty * 10**qty_places) / 10**qty_places
                qty = round(qty, qty_places)
                if qty <= 0:
                    continue
                levels.append(GridLevel(price=gp, qty=qty, side="sell"))

        return levels

    def _place_orders(
        self, session: GridSession,
        price_places: int = 2, qty_places: int = 6,
    ) -> tuple[int, int, str]:
        placed = 0
        failed = 0
        first_error = ""

        for lvl in session.levels:
            try:
                resp = self.client.place_limit_order(
                    session.config.symbol, lvl.side,
                    lvl.price, lvl.qty,
                    price_places=price_places,
                    qty_places=qty_places,
                )
                order_id = resp.get("orderId", "")
                if not order_id:
                    raise RuntimeError("لم يُعاد orderId من BitGet")
                lvl.order_id = order_id
                order_db = db.create_order({
                    "id":         str(uuid.uuid4()),
                    "session_id": session.id,
                    "order_id":   order_id,
                    "side":       lvl.side,
                    "price":      lvl.price,
                    "qty":        lvl.qty,
                    "status":     "open",
                })
                lvl.db_id = order_db["id"]
                placed += 1
            except Exception as e:
                err_str = str(e)
                # FIX: use clean f-string instead of mixed .format()/%s logger pattern
                logger.error(
                    "Place order failed price=%s %s qty=%s: %s",
                    f"{lvl.price:.{price_places}f}", lvl.side,
                    f"{lvl.qty:.{qty_places}f}", err_str,
                )
                if not first_error:
                    first_error = err_str
                lvl.status = "failed"
                failed += 1

        return placed, failed, first_error

    def refresh_session(self, session: GridSession) -> None:
        # FIX (performance): old code called get_order() for EVERY open order
        # individually — 50 orders × every 60s = 50 API calls/min just to check
        # fills. New approach: one get_open_orders() call gives us all still-open
        # order IDs; only orders *missing* from that set need a status check.
        # Reduces API calls from N to 1+filled_count per refresh cycle.
        try:
            open_ids = {
                o.get("orderId", o.get("order_id", ""))
                for o in self.client.get_open_orders(session.config.symbol)
            }
            batch_ok = True
        except Exception as e:
            logger.warning("get_open_orders failed, falling back to per-order: %s", e)
            open_ids  = set()
            batch_ok  = False

        # FIX: iterate over a *copy* of session.levels because _handle_filled()
        # calls session.levels.append(), and mutating a list during iteration
        # causes new counter-orders to be re-checked in the same loop.
        for lvl in list(session.levels):
            if lvl.status != "open" or not lvl.order_id:
                continue
            # If we got a valid open-orders list AND this order is still open → skip
            if batch_ok and lvl.order_id in open_ids:
                continue
            try:
                info  = self.client.get_order(session.config.symbol, lvl.order_id)
                state = info.get("status", "")
                if state in ("full_fill", "filled", "FullFill", "Filled"):
                    lvl.status = "filled"
                    if lvl.db_id:
                        db.update_order(lvl.db_id, {
                            "status":    "filled",
                            "filled_at": datetime.now(timezone.utc).isoformat(),
                        })
                    self._handle_filled(session, lvl)
                elif state in ("cancelled", "cancel", "Cancelled", "Cancel",
                               "partial_cancel", "PartialCancel"):
                    logger.warning(
                        "Order %s (session %s) was cancelled on BitGet — "
                        "marking cancelled in DB", lvl.order_id, session.id[:8],
                    )
                    lvl.status = "cancelled"
                    if lvl.db_id:
                        db.update_order(lvl.db_id, {"status": "cancelled"})
            except Exception as e:
                err_str = str(e)
                # FIX: 404 = order no longer exists on BitGet (filled externally,
                # cancelled, or expired). Stop re-checking it every minute by
                # marking it "not_found". Without this fix the same dead order IDs
                # flood the logs with HTTP 404 errors on every refresh cycle.
                if "HTTP 404" in err_str or "40404" in err_str:
                    # BitGet returns 40404 for BOTH cancelled AND filled orders
                    # when queried via order-info. Check history before giving up.
                    hist = self.client.get_history_order(
                        session.config.symbol, lvl.order_id
                    )
                    if hist:
                        hist_state = hist.get("status", "")
                        if hist_state in ("full_fill", "filled", "FullFill", "Filled"):
                            logger.info(
                                "Order %s (%s / session %s) was FILLED (found in history) "
                                "— placing counter-order.",
                                lvl.order_id, session.config.symbol, session.id[:8],
                            )
                            lvl.status = "filled"
                            if lvl.db_id:
                                db.update_order(lvl.db_id, {
                                    "status":    "filled",
                                    "filled_at": datetime.now(timezone.utc).isoformat(),
                                })
                            self._handle_filled(session, lvl)
                            continue
                        else:
                            logger.warning(
                                "Order %s (%s / session %s) found in history with "
                                "status=%s — marking cancelled.",
                                lvl.order_id, session.config.symbol,
                                session.id[:8], hist_state,
                            )
                            lvl.status = "cancelled"
                            if lvl.db_id:
                                db.update_order(lvl.db_id, {"status": "cancelled"})
                    else:
                        # Truly not found anywhere — stop re-checking.
                        logger.warning(
                            "Order %s (%s / session %s) returned 404 and not in "
                            "history — marking not_found.",
                            lvl.order_id, session.config.symbol, session.id[:8],
                        )
                        lvl.status = "not_found"
                        if lvl.db_id:
                            db.update_order(lvl.db_id, {"status": "not_found"})
                else:
                    logger.warning("Order refresh error: %s", err_str)

        pnl = db.session_total_pnl(session.id)
        if pnl != session.total_pnl:   # only write if value changed
            session.total_pnl = pnl
            db.update_session(session.id, {"total_pnl": pnl})

    def _handle_filled(self, session: GridSession, filled: GridLevel) -> None:
        cfg      = session.config
        infinite = cfg.step_pct > 0
        pp, qp   = self.client.get_symbol_precision(cfg.symbol)

        if infinite:
            # Infinite mode: step is always base_price × step_pct%
            step = round(session.base_price * cfg.step_pct / 100, pp)
        else:
            n    = cfg.grid_count
            step = round((session.upper_price - session.lower_price) / n, pp)

        if step <= 0:
            logger.error(
                "_handle_filled: step=0 for session %s (%s) — aborting counter-order.",
                session.id[:8], cfg.symbol,
            )
            return

        if filled.side == "buy":
            # Infinite mode: Arithmetic (fixed step)
            if infinite:
                new_price = round(filled.price + step, pp)
            else:
                new_price = round(filled.price + step, pp)
            # Infinite mode: respect upper_limit_price if set; else no upper bound.
            if infinite:
                in_range = (cfg.upper_limit_price <= 0 or new_price <= cfg.upper_limit_price)
            else:
                in_range = (new_price <= session.upper_price)
            if in_range:
                try:
                    # BingX Infinity: maintain constant USDT value.
                    if infinite:
                        amt = max(cfg.entry_amount, BITGET_MIN_NOTIONAL)
                        raw_qty = amt / new_price
                        qty = math.ceil(raw_qty * 10**qp) / 10**qp
                        qty = round(qty, qp)
                    else:
                        qty = filled.qty

                    resp = self.client.place_limit_order(
                        cfg.symbol, "sell", new_price, qty,
                        price_places=pp, qty_places=qp,
                    )
                    new_lvl = GridLevel(
                        price=new_price, qty=filled.qty,
                        side="sell", order_id=resp.get("orderId", ""),
                        entry_price=filled.price,
                    )
                    order_db = db.create_order({
                        "id": str(uuid.uuid4()), "session_id": session.id,
                        "order_id": new_lvl.order_id, "side": "sell",
                        "price": new_price, "qty": filled.qty,
                        "entry_price": filled.price,
                        "status": "open",
                    })
                    new_lvl.db_id = order_db["id"]
                    session.levels.append(new_lvl)
                    logger.info("Counter-sell placed for %s at %.6f", cfg.symbol, new_price)
                except Exception as e:
                    logger.error("Counter-sell failed for %s: %s", cfg.symbol, e)

        elif filled.side == "sell":
            if filled.entry_price is not None:
                pnl = round((filled.price - filled.entry_price) * filled.qty, 6)
                try:
                    db.create_trade({
                        "id":         str(uuid.uuid4()),
                        "session_id": session.id,
                        "buy_price":  filled.entry_price,
                        "sell_price": filled.price,
                        "qty":        filled.qty,
                        "pnl":        pnl,
                    })
                except Exception as e:
                    logger.error("Trade record failed: %s", e)

            # Counter-buy: one step below the filled sell price
            if infinite:
                # Arithmetic: new_price = filled_price - fixed_step
                new_price = round(filled.price - step, pp)
            else:
                new_price = round(filled.price - step, pp)

            if infinite:
                # Infinite mode: only hard lower limit stops us.
                in_range = (cfg.lower_limit_price <= 0 or new_price >= cfg.lower_limit_price)
            else:
                in_range = (new_price >= session.lower_price)

            if in_range:
                try:
                    # BingX Infinity: maintain constant USDT value.
                    # We may need to adjust qty slightly if the price moved significantly,
                    # but for a single step, using the filled.qty is usually fine.
                    # However, to be strict with "constant value", we can recalculate:
                    if infinite:
                        amt = max(cfg.entry_amount, BITGET_MIN_NOTIONAL)
                        raw_qty = amt / new_price
                        qty = math.ceil(raw_qty * 10**qp) / 10**qp
                        qty = round(qty, qp)
                    else:
                        qty = filled.qty

                    resp = self.client.place_limit_order(
                        cfg.symbol, "buy", new_price, qty,
                        price_places=pp, qty_places=qp,
                    )
                    new_lvl = GridLevel(
                        price=new_price, qty=filled.qty,
                        side="buy", order_id=resp.get("orderId", ""),
                    )
                    order_db = db.create_order({
                        "id": str(uuid.uuid4()), "session_id": session.id,
                        "order_id": new_lvl.order_id, "side": "buy",
                        "price": new_price, "qty": filled.qty, "status": "open",
                    })
                    new_lvl.db_id = order_db["id"]
                    session.levels.append(new_lvl)
                    logger.info("Counter-buy placed for %s at %.6f", cfg.symbol, new_price)

                    # ── Grid walk-up ───────────────────────────────────────────────
                    # After each sell fills and counter-buy is placed, retire the
                    # lowest stale buy so the grid rises with the price.
                    # Threshold: any buy more than 2×levels_per_side steps below the
                    # new counter-buy is considered stale and gets cancelled.
                    if infinite:
                        open_buys = sorted(
                            [l for l in session.levels
                             if l.side == "buy" and l.status == "open"
                             and l.order_id and l is not new_lvl],
                            key=lambda x: x.price,
                        )
                        # Threshold: if we have more than levels_per_side buy orders, 
                        # retire the lowest one to keep the grid size constant.
                        if len(open_buys) >= cfg.levels_per_side:
                            stale = open_buys[0]
                            try:
                                self.client.cancel_order(cfg.symbol, stale.order_id)
                                stale.status = "cancelled"
                                if stale.db_id:
                                    db.update_order(stale.db_id, {"status": "cancelled"})
                                logger.info(
                                    "Grid walk-up: retired stale buy at %.6f for %s",
                                    stale.price, cfg.symbol,
                                )
                            except Exception as e:
                                logger.warning(
                                    "Grid walk-up cancel failed for %s: %s", cfg.symbol, e
                                )

                except Exception as e:
                    logger.error("Counter-buy failed for %s: %s", cfg.symbol, e)

    def recover_not_found_orders(self) -> list[dict]:
        """
        Scan every not_found order across all active sessions.
        Check BitGet history for each one — if it was actually filled,
        place the counter-order immediately.
        Returns list of recovered dicts: {session_id, symbol, side, price, action}
        """
        recovered = []
        for session in self.all_active():
            not_found_lvls = [
                l for l in session.levels
                if l.status == "not_found" and l.order_id
            ]
            for lvl in not_found_lvls:
                try:
                    hist = self.client.get_history_order(
                        session.config.symbol, lvl.order_id
                    )
                    if not hist:
                        continue
                    state = hist.get("status", "")
                    if state in ("full_fill", "filled", "FullFill", "Filled"):
                        logger.info(
                            "recover: order %s (%s / session %s) was FILLED — "
                            "placing counter-order now.",
                            lvl.order_id, session.config.symbol, session.id[:8],
                        )
                        lvl.status = "filled"
                        if lvl.db_id:
                            db.update_order(lvl.db_id, {
                                "status":    "filled",
                                "filled_at": datetime.now(timezone.utc).isoformat(),
                            })
                        self._handle_filled(session, lvl)
                        recovered.append({
                            "session_id": session.id,
                            "symbol":     session.config.symbol,
                            "side":       lvl.side,
                            "price":      lvl.price,
                            "action":     "counter_placed",
                        })
                except Exception as e:
                    logger.warning(
                        "recover: error checking order %s (%s): %s",
                        lvl.order_id, session.config.symbol, e,
                    )
        return recovered

    def rebalance_stale_orders(self, session: GridSession) -> dict:
        """
        Single-call tracker: fetches price & precision ONCE then handles
        both sides — moves lowest stale buy up and highest stale sell down.
        No-op for legacy fixed-range grids (step_pct == 0).
        Returns: {'buy_cancelled','buy_placed','sell_cancelled','sell_placed','errors'}
        """
        cfg = session.config
        if cfg.step_pct <= 0:
            return {"buy_cancelled": 0, "buy_placed": 0,
                    "sell_cancelled": 0, "sell_placed": 0, "errors": 0}

        pp, qp    = self.client.get_symbol_precision(cfg.symbol)   # cached
        cur_price = self.client.get_price(cfg.symbol)              # ONE call
        step      = round(session.base_price * cfg.step_pct / 100, pp)

        # ── FIX: Safety check for zero price or step to avoid DivisionByZero ──
        if step <= 0 or cur_price <= 0 or session.base_price <= 0:
            logger.warning("rebalance: skipping %s due to zero price/step (P:%.6f, S:%.6f)", 
                           cfg.symbol, cur_price, step)
            return {"buy_cancelled": 0, "buy_placed": 0,
                    "sell_cancelled": 0, "sell_placed": 0, "errors": 0}

        buy_threshold  = cur_price - cfg.levels_per_side * step * 1.5
        sell_threshold = cur_price + cfg.levels_per_side * step * 1.5

        stale_buys  = sorted(
            [l for l in session.levels
             if l.side == "buy"  and l.status == "open"
             and l.order_id and l.price < buy_threshold],
            key=lambda x: x.price
        )
        stale_sells = sorted(
            [l for l in session.levels
             if l.side == "sell" and l.status == "open"
             and l.order_id and l.price > sell_threshold],
            key=lambda x: x.price, reverse=True
        )

        if not stale_buys and not stale_sells:
            return {"buy_cancelled": 0, "buy_placed": 0,
                    "sell_cancelled": 0, "sell_placed": 0, "errors": 0}

        occ_buys  = {round(l.price, pp) for l in session.levels
                     if l.side == "buy"  and l.status == "open" and l not in stale_buys}
        occ_sells = {round(l.price, pp) for l in session.levels
                     if l.side == "sell" and l.status == "open" and l not in stale_sells}

        bc = bp = sc = sp = errors = 0

        # ── BUY side: move lowest stale buy up ─────────────────────────────
        for stale in stale_buys:
            try:
                self.client.cancel_order(cfg.symbol, stale.order_id)
                stale.status = "cancelled"
                if stale.db_id:
                    db.update_order(stale.db_id, {"status": "cancelled"})
                bc += 1
                logger.info("track: cancelled stale buy  %.6f  %s", stale.price, cfg.symbol)
            except Exception as e:
                logger.warning("track: cancel buy failed %s %.6f: %s", cfg.symbol, stale.price, e)
                errors += 1
                continue

            new_price = None
            for i in range(1, cfg.levels_per_side * 3 + 1):
                c = round(cur_price - i * step, pp)
                if c <= 0: break
                if cfg.lower_limit_price > 0 and c < cfg.lower_limit_price: break
                if c not in occ_buys:
                    new_price = c
                    occ_buys.add(c)
                    break

            if new_price is None:
                continue
            try:
                if new_price <= 0: continue
                # FIX: enforce minimum 1 USDT notional on rebalanced buy orders
                effective_amt = max(cfg.entry_amount, BITGET_MIN_NOTIONAL)
                raw_qty = effective_amt / new_price
                qty = math.ceil(raw_qty * 10**qp) / 10**qp
                qty = round(qty, qp)
                resp = self.client.place_limit_order(
                    cfg.symbol, "buy", new_price, qty, price_places=pp, qty_places=qp)
                lvl  = GridLevel(price=new_price, qty=qty, side="buy",
                                 order_id=resp.get("orderId", ""))
                rec  = db.create_order({"id": str(uuid.uuid4()), "session_id": session.id,
                                        "order_id": lvl.order_id, "side": "buy",
                                        "price": new_price, "qty": qty, "status": "open"})
                lvl.db_id = rec["id"]
                session.levels.append(lvl)
                bp += 1
                logger.info("track: placed buy  %.6f  %s", new_price, cfg.symbol)
            except Exception as e:
                logger.error("track: place buy failed %s %.6f: %s", cfg.symbol, new_price, e)
                errors += 1

        # ── SELL side: move highest stale sell down ─────────────────────────
        for stale in stale_sells:
            try:
                self.client.cancel_order(cfg.symbol, stale.order_id)
                stale.status = "cancelled"
                if stale.db_id:
                    db.update_order(stale.db_id, {"status": "cancelled"})
                sc += 1
                logger.info("track: cancelled stale sell %.6f  %s", stale.price, cfg.symbol)
            except Exception as e:
                logger.warning("track: cancel sell failed %s %.6f: %s", cfg.symbol, stale.price, e)
                errors += 1
                continue

            new_price = None
            for i in range(1, cfg.levels_per_side * 3 + 1):
                c = round(cur_price + i * step, pp)
                if cfg.upper_limit_price > 0 and c > cfg.upper_limit_price: break
                if c not in occ_sells:
                    new_price = c
                    occ_sells.add(c)
                    break

            if new_price is None:
                continue
            try:
                qty  = stale.qty
                resp = self.client.place_limit_order(
                    cfg.symbol, "sell", new_price, qty, price_places=pp, qty_places=qp)
                lvl  = GridLevel(price=new_price, qty=qty, side="sell",
                                 order_id=resp.get("orderId", ""))
                rec  = db.create_order({"id": str(uuid.uuid4()), "session_id": session.id,
                                        "order_id": lvl.order_id, "side": "sell",
                                        "price": new_price, "qty": qty, "status": "open"})
                lvl.db_id = rec["id"]
                session.levels.append(lvl)
                sp += 1
                logger.info("track: placed sell %.6f  %s", new_price, cfg.symbol)
            except Exception as e:
                logger.error("track: place sell failed %s %.6f: %s", cfg.symbol, new_price, e)
                errors += 1

        return {"buy_cancelled": bc, "buy_placed": bp,
                "sell_cancelled": sc, "sell_placed": sp, "errors": errors}



    def close_session(self, session: GridSession, reason: str = "manual",
                      market_sell: bool = False) -> float:
        # Use batch cancel (cancel-symbol-order) to atomically cancel ALL open orders
        # for this symbol in one API call, avoiding the 20-order pagination limit of
        # the old unfilled-orders approach.
        # Note: this cancels all orders for the symbol on BitGet — if two sessions share
        # a symbol simultaneously they will both be closed, which is acceptable since
        # each close_session() call always cleans up its own orders via DB afterward.
        # FIX: snapshot session.levels with list() before iterating — rebalance_stale_orders
        # runs in a parallel thread and appends new levels to the same list.
        sym = session.config.symbol
        batch_ok = self.client.cancel_symbol_orders_batch(sym)
        if not batch_ok:
            # Fallback: cancel known session orders one by one
            for lvl in list(session.levels):
                if lvl.status == "open" and lvl.order_id:
                    try:
                        self.client.cancel_order(sym, lvl.order_id)
                    except Exception as e:
                        if "404" not in str(e):
                            logger.warning("Cancel order %s: %s", lvl.order_id[:8], e)

        # Update in-memory state and DB for all open orders in this session
        for lvl in list(session.levels):
            if lvl.status == "open" and lvl.order_id:
                lvl.status = "cancelled"
                if lvl.db_id:
                    db.update_order(lvl.db_id, {"status": "cancelled"})
        db.cancel_session_orders(session.id)

        # بيع العملة بسعر السوق بعد إلغاء الأوامر.
        #
        # FIX: لا نحسب الكمية من session.levels لأن هذا يُخطئ في حالتين:
        #   1. أوامر بيع أولية فشل إرسالها (status="failed") — التوكنات اشتريت
        #      لكن لا أمر يتتبعها في session.levels.
        #   2. counter-sell فشل بعد اكتمال شراء — التوكنات في المحفظة بلا تتبع.
        #
        # الحل: بعد إلغاء الأوامر ننتظر تحرر الرصيد المجمد ثم نسأل BitGet عن
        # الرصيد المتاح فعلاً (available).
        # "available" = الرصيد الحر فقط، لا يشمل ما هو مقفل في أوامر جلسات أخرى،
        # لذا هو آمن حتى عند وجود جلستين على نفس الرمز.

        # نحفظ نتيجة البيع في session._sell_ok لاستخدامها في monitor.py
        # عند بناء الإشعار المناسب (نجاح / فشل).
        session._sell_ok    = True
        session._sell_error = ""

        if market_sell:
            sym  = session.config.symbol
            base = sym[:-4] if sym.endswith("USDT") else sym[:-3]
            try:
                _, qp = self.client.get_symbol_precision(sym)

                # FIX: بعد إلغاء الأوامر، BitGet لا يُحرر الرصيد المجمد فوراً.
                # زدنا مدة الانتظار لـ 15 ثانية مع تحسين منطق الكشف عن الرصيد.
                import time as _time
                deadline = _time.monotonic() + 15
                available = 0.0
                while _time.monotonic() < deadline:
                    assets = self.client.get_account_balance()
                    target_asset = next((a for a in assets if (a.get("coin") or a.get("coinName", "")) == base), None)
                    
                    if target_asset:
                        available  = float(target_asset.get("available", 0))
                        frozen_val = float(target_asset.get("frozen", 0) or target_asset.get("locked", 0))
                    else:
                        available, frozen_val = 0.0, 0.0

                    # إذا توفر رصيد قابل للبيع أو اختفى المجمد تماماً، نخرج فوراً
                    if available > 1e-8 or (frozen_val < 1e-8 and available == 0):
                        break
                        
                    logger.info("close_session [%s]: waiting for frozen balance — avail=%.8f frozen=%.8f",
                                session.id[:8], available, frozen_val)
                    _time.sleep(1.5)
                else:
                    logger.warning("close_session [%s]: timeout waiting for frozen balance; available=%.8f",
                                   session.id[:8], available)

                # تحقق من الحد الأدنى للكمية قبل المحاولة
                min_base = self.client.get_min_base_qty(sym)
                # FIX: use floor (truncate) instead of round — rounding UP causes
                # BitGet to reject with 43012 because we ask to sell more than available.
                qty_rounded = math.floor(available * 10**qp) / 10**qp

                if qty_rounded <= 0:
                    session._sell_ok    = False
                    session._sell_error = f"رصيد متاح = 0 بعد الانتظار"
                    logger.warning(
                        "Market sell on close [%s]: %s — no available balance (%.8f raw)",
                        session.id[:8], sym, available,
                    )
                elif min_base > 0 and qty_rounded < min_base:
                    session._sell_ok    = False
                    session._sell_error = (
                        f"الكمية {qty_rounded} أقل من الحد الأدنى {min_base} — dust"
                    )
                    logger.warning(
                        "Market sell on close [%s]: %s — qty %.8f < min base %.8f (dust)",
                        session.id[:8], sym, qty_rounded, min_base,
                    )
                else:
                    self.client.place_market_sell(sym, qty_rounded, qty_places=qp)
                    logger.info(
                        "Market sell on close [%s]: %s qty=%.6f ✅",
                        session.id[:8], sym, qty_rounded,
                    )

            except Exception as e:
                session._sell_ok    = False
                session._sell_error = str(e)[:200]
                logger.error(
                    "Market sell on close [%s] failed (%s): %s",
                    session.id[:8], sym, e,
                )

        pnl = db.session_total_pnl(session.id)
        db.close_session(session.id, pnl)
        session.status = "closed"
        session.total_pnl = pnl
        self._sessions.pop(session.id, None)
        logger.info(
            "Session %s closed (%s) P&L=%.6f sell_ok=%s",
            session.id[:8], reason, pnl, session._sell_ok,
        )
        return pnl

    def get_session(self, session_id: str) -> Optional[GridSession]:
        return self._sessions.get(session_id)

    def all_active(self) -> list[GridSession]:
        return [s for s in self._sessions.values() if s.status == "active"]

    def load_from_db(self) -> None:
        rows = db.list_active_sessions()
        if not rows:
            return
        for row in rows:
            cfg = GridConfig(
                symbol=row["symbol"], entry_amount=float(row["entry_amount"]),
                step_pct=float(row.get("step_pct") or 0),
                levels_per_side=int(row.get("levels_per_side") or 0),
                lower_limit_price=float(row.get("lower_limit_price") or 0),
                upper_limit_price=float(row.get("upper_limit_price") or 0),
                upper_pct=float(row["upper_pct"]), lower_pct=float(row["lower_pct"]),
                grid_count=int(row["grid_count"]), profit_target=float(row["profit_target"]),
                stop_loss=float(row["stop_loss"]), depth=int(row["depth"]),
                parent_id=row.get("parent_id"),
                group_id=row.get("group_id"),
                trailing_stop=bool(row.get("trailing_stop", False)),
                trailing_pct=float(row.get("trailing_pct", 0)),
            )
            session = GridSession(
                id=row["id"], config=cfg,
                base_price=float(row["base_price"]),
                upper_price=float(row["upper_price"]),
                lower_price=float(row["lower_price"]),
                total_pnl=float(row["total_pnl"]),
                status=row["status"],
            )
            try:
                for o in db.get_session_orders(row["id"]):
                    if o.get("status") == "open" and o.get("order_id"):
                        # FIX: restore entry_price for sell orders so P&L is
                        # recorded correctly even if bot restarted mid-trade.
                        ep = o.get("entry_price")
                        session.levels.append(GridLevel(
                            price=float(o["price"]), qty=float(o["qty"]),
                            side=o["side"], order_id=o["order_id"],
                            db_id=o["id"], status="open",
                            entry_price=float(ep) if ep is not None else None,
                        ))
            except Exception as e:
                logger.warning("Reload orders for %s: %s", row["id"][:8], e)
            self._sessions[session.id] = session
            logger.info(
                "Loaded %s (%s) %d orders",
                session.id[:8], cfg.symbol, len(session.levels),
            )
        logger.info("Loaded %d sessions from DB", len(rows))


_engine: Optional[GridEngine] = None

def get_engine() -> GridEngine:
    global _engine
    if _engine is None:
        _engine = GridEngine()
    return _engine
