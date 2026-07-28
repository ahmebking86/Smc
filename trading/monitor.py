"""
Background monitor — runs in asyncio loop.
Polls active sessions, checks stop-loss / profit targets / trailing stop.
Sends Telegram alerts when action is taken.
"""

from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

from config import MONITOR_INTERVAL, TELEGRAM_CHAT_ID
from trading.grid_engine import get_engine

if TYPE_CHECKING:
    from telegram import Bot

logger = logging.getLogger(__name__)

# ── Stop-loss warning state ────────────────────────────────────────────────────
# Tracks per-session which warnings have already been sent this "danger episode".
# Values: "none" | "warn50" | "warn80"
# Resets to "none" when price recovers above 40% of the SL threshold.
_sl_warn: dict[str, str] = {}          # session_id → current warning level

# ── Rebalance pause flag ───────────────────────────────────────────────────────
# Set to True during close-all / liquidate operations to prevent rebalance from
# placing new limit orders that would re-freeze balances right before a market sell.
_rebalance_paused: bool = False


def pause_rebalance() -> None:
    """Block rebalance_stale_orders from running (call before any close/liquidate)."""
    global _rebalance_paused
    _rebalance_paused = True
    logger.info("Monitor: rebalance PAUSED")


def resume_rebalance() -> None:
    """Re-enable rebalance_stale_orders after close/liquidate is complete."""
    global _rebalance_paused
    _rebalance_paused = False
    logger.info("Monitor: rebalance RESUMED")



async def _notify(bot: "Bot", text: str) -> None:
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("Notify failed: %s", e)


async def monitor_loop(bot: "Bot") -> None:
    engine = get_engine()
    logger.info("Monitor started (interval=%ds)", MONITOR_INTERVAL)
    while True:
        try:
            await _tick(engine, bot)
        except Exception as e:
            logger.error("Monitor tick error: %s", e)
        await asyncio.sleep(MONITOR_INTERVAL)


async def _tick(engine, bot: "Bot") -> None:
    sessions = [s for s in engine.all_active() if s.status == "active"]
    if not sessions:
        return

    # ── FIX: Process sessions in parallel ────────────────────────────────────
    # Instead of sequential processing, we run them all concurrently.
    # A semaphore limits the number of simultaneous BitGet API calls to prevent rate-limiting.
    _sem = asyncio.Semaphore(10)

    async def _safe_process(session):
        async with _sem:
            try:
                await _process_session(engine, bot, session)
            except Exception as e:
                logger.error(
                    "Unhandled error processing session %s (%s): %s",
                    session.id[:8], session.config.symbol, e,
                )

    await asyncio.gather(*[_safe_process(s) for s in sessions])


async def _process_session(engine, bot: "Bot", session) -> None:
    # FIX: Skip ALL processing when a close/liquidate operation is running.
    # pause_rebalance() was previously only guarding rebalance_stale_orders, but
    # refresh_session() also places counter-orders via _handle_filled(). If a buy
    # order fills just before liquidation, the monitor detects it and places a
    # counter-sell — re-freezing the base token balance right in the middle of the
    # 30-second wait in liquidate_wallet(), causing persistent 43012 errors.
    # Pausing the entire tick is the safest fix: the session state is fully rebuilt
    # from the DB on resume, so no events are lost.
    if _rebalance_paused:
        logger.debug("Monitor: tick fully skipped for %s (paused)", session.config.symbol)
        return

    # 1. Refresh filled orders and update realized P&L
    # We use asyncio.to_thread to keep the event loop responsive.
    await asyncio.to_thread(engine.refresh_session, session)

    # 1b. Auto-track both sides in ONE API call (price fetched once):
    #   • price rises  → lowest stale buy  moves up
    #   • price drops  → highest stale sell moves down
    await asyncio.to_thread(engine.rebalance_stale_orders, session)

    entry   = session.config.entry_amount
    pnl_pct = (session.total_pnl / entry * 100) if entry else 0

    # 2a. Trailing stop — close if profit drops trailing_pct from peak
    if session.config.trailing_stop and session.config.trailing_pct > 0 and pnl_pct > 0:
        peak = getattr(session, "_peak_pnl", 0.0)
        if pnl_pct > peak:
            session._peak_pnl = pnl_pct
            peak = pnl_pct
        if peak > 0 and (peak - pnl_pct) >= session.config.trailing_pct:
            pnl = await asyncio.to_thread(engine.close_session, session, "trailing_stop",
                                          True)
            sell_ok  = getattr(session, "_sell_ok",    True)
            sell_err = getattr(session, "_sell_error", "")
            if sell_ok:
                await _notify(
                    bot,
                    f"📉 <b>Trailing Stop تفعّل!</b>\n"
                    f"زوج: <code>{session.config.symbol}</code>\n"
                    f"أعلى ربح بلغ: <b>+{peak:.2f}%</b> ثم تراجع لـ <b>{pnl_pct:.2f}%</b>\n"
                    f"الربح المحقق: <b>{pnl:.4f} USDT</b>\n"
                    f"🔒 تم إغلاق الشبكة."
                )
            else:
                await _notify(
                    bot,
                    f"📉 <b>Trailing Stop تفعّل — لكن البيع فشل!</b>\n"
                    f"زوج: <code>{session.config.symbol}</code>\n"
                    f"⚠️ الجلسة أُغلقت في النظام لكن العملة <b>لم تُبَع</b> على BitGet.\n"
                    f"🔧 السبب: <code>{sell_err[:120]}</code>\n"
                    f"👉 بع <b>{session.config.symbol[:-4]}</b> يدوياً من المنصة أو استخدم زر تصفية المحفظة."
                )
            return

    # 2b. Price-based take profit → close when price rises X% from entry
    if session.config.profit_target > 0 and session.base_price > 0:
        try:
            current_price = await asyncio.to_thread(engine.client.get_price, session.config.symbol)
            rise_pct = (current_price - session.base_price) / session.base_price * 100
            if rise_pct >= session.config.profit_target:
                pnl = await asyncio.to_thread(engine.close_session, session, "profit_target", True)
                sell_ok  = getattr(session, "_sell_ok",    True)
                sell_err = getattr(session, "_sell_error", "")
                if sell_ok:
                    await _notify(
                        bot,
                        f"🎯 <b>هدف الربح محقق!</b>\n"
                        f"زوج: <code>{session.config.symbol}</code>\n"
                        f"ارتفع السعر <b>+{rise_pct:.2f}%</b> من سعر البداية\n"
                        f"<code>{session.base_price:.6f}</code> → <code>{current_price:.6f}</code>\n"
                        f"الربح المحقق: <b>{pnl:.4f} USDT</b>\n"
                        f"🔒 تم إغلاق الشبكة."
                    )
                else:
                    await _notify(
                        bot,
                        f"🎯 <b>هدف الربح محقق — لكن البيع فشل!</b>\n"
                        f"زوج: <code>{session.config.symbol}</code>\n"
                        f"⚠️ الجلسة أُغلقت في النظام لكن العملة <b>لم تُبَع</b> على BitGet.\n"
                        f"🔧 السبب: <code>{sell_err[:120]}</code>\n"
                        f"👉 بع <b>{session.config.symbol[:-4]}</b> يدوياً أو استخدم زر تصفية المحفظة."
                    )
                return
        except Exception as e:
            logger.warning("Take profit price check failed for %s: %s", session.config.symbol, e)

    # 3. استخدام السعر الذي تم جلبه بالفعل أثناء التحديث (إذا كان متاحاً)
    # لتجنب طلب API إضافي في كل دورة لكل زوج.
    try:
        current_price = await asyncio.to_thread(
            engine.client.get_price, session.config.symbol
        )
    except Exception as e:
        logger.warning("Price fetch failed (%s): %s", session.config.symbol, e)
        return

    # 3a. وقف الخسارة بناءً على انخفاض السعر عن سعر البداية
    if session.base_price > 0:
        drop_pct = (session.base_price - current_price) / session.base_price * 100
        sl        = session.config.stop_loss

        if sl > 0:
            # ── diagnostic log every tick ────────────────────────────────────
            logger.info(
                "SL-check %s: base=%.6f  cur=%.6f  drop=%.2f%%  threshold=%.1f%%  → %s",
                session.config.symbol,
                session.base_price, current_price,
                drop_pct, sl,
                "🔴 TRIGGER" if drop_pct >= sl else "✅ OK",
            )

            sid          = session.id
            warn_state   = _sl_warn.get(sid, "none")

            # Reset warning when price recovers to < 40 % of threshold
            if drop_pct < sl * 0.40 and warn_state != "none":
                _sl_warn[sid] = "none"
                warn_state    = "none"

            # 1st warning — reached 50 % of threshold
            if warn_state == "none" and drop_pct >= sl * 0.50:
                _sl_warn[sid] = "warn50"
                await _notify(
                    bot,
                    f"⚠️ <b>تنبيه أول — اقتراب من وقف الخسارة</b>\n"
                    f"زوج: <code>{session.config.symbol}</code>\n"
                    f"📉 الانخفاض الحالي: <b>{drop_pct:.2f}%</b>  من حد <b>{sl}%</b>\n"
                    f"💵 سعر البداية: <code>{session.base_price:.6f}</code>\n"
                    f"💵 السعر الحالي: <code>{current_price:.6f}</code>\n"
                    f"🟡 وصل إلى <b>50%</b> من حد وقف الخسارة — راقب الوضع."
                )
                warn_state = "warn50"

            # 2nd warning — reached 80 % of threshold
            if warn_state == "warn50" and drop_pct >= sl * 0.80:
                _sl_warn[sid] = "warn80"
                await _notify(
                    bot,
                    f"🚨 <b>تنبيه عاجل — على وشك وقف الخسارة!</b>\n"
                    f"زوج: <code>{session.config.symbol}</code>\n"
                    f"📉 الانخفاض الحالي: <b>{drop_pct:.2f}%</b>  من حد <b>{sl}%</b>\n"
                    f"💵 سعر البداية: <code>{session.base_price:.6f}</code>\n"
                    f"💵 السعر الحالي: <code>{current_price:.6f}</code>\n"
                    f"🔴 وصل إلى <b>80%</b> من الحد — وقف الخسارة سيتفعل قريباً!"
                )

        # ── Actual stop-loss trigger ─────────────────────────────────────────
        if sl > 0 and drop_pct >= sl:
            _sl_warn.pop(session.id, None)          # clear warning state
            pnl = await asyncio.to_thread(
                engine.close_session, session, "stop_loss", True
            )
            sell_ok  = getattr(session, "_sell_ok",    True)
            sell_err = getattr(session, "_sell_error", "")
            if sell_ok:
                await _notify(
                    bot,
                    f"🛑 <b>وقف الخسارة تفعّل!</b>\n"
                    f"زوج: <code>{session.config.symbol}</code>\n"
                    f"السعر الحالي: <code>{current_price:.6f}</code>\n"
                    f"انخفض <b>{drop_pct:.1f}%</b> من سعر البداية "
                    f"<code>{session.base_price:.6f}</code>\n"
                    f"الخسارة المحققة: <b>{pnl:.4f} USDT</b>\n"
                    f"🔒 تم إغلاق الشبكة وبيع العملة."
                )
            else:
                # FIX: البيع فشل على BitGet — ننبه المستخدم بوضوح بدل إشعار النجاح المضلل
                await _notify(
                    bot,
                    f"🛑 <b>وقف الخسارة تفعّل — لكن البيع فشل على المنصة!</b>\n"
                    f"زوج: <code>{session.config.symbol}</code>\n"
                    f"السعر الحالي: <code>{current_price:.6f}</code>\n"
                    f"انخفض <b>{drop_pct:.1f}%</b> من سعر البداية "
                    f"<code>{session.base_price:.6f}</code>\n\n"
                    f"⚠️ الجلسة أُغلقت في النظام لكن <b>{session.config.symbol[:-4]} لم تُبَع</b>.\n"
                    f"🔧 السبب: <code>{sell_err[:150]}</code>\n\n"
                    f"👉 <b>تصرف الآن:</b> بع {session.config.symbol[:-4]} يدوياً من BitGet\n"
                    f"أو استخدم زر <b>💰 تصفية المحفظة</b> من القائمة الرئيسية."
                )
            return


