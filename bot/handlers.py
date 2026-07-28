"""
All Telegram handlers — commands, callbacks, conversation flows.
"""

from __future__ import annotations
import asyncio
import html as _html
import uuid as _uuid
import logging
import time
from math import floor
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, CallbackQueryHandler, MessageHandler, filters,
)

from config import TELEGRAM_CHAT_ID
import bot.live_tracker as live_tracker
from bot.keyboards import (
    main_menu, confirm_cancel, back_main,
    sessions_list, session_actions, close_confirm,
    close_all_confirm_kb, liquidate_wallet_confirm_kb,
    templates_menu_kb, tpl_info_kb, tpl_limit_type_kb, tpl_trailing_kb, tpl_edit_menu_kb,
    test_sl_confirm_kb, session_edit_kb,
    bulk_scope_kb, bulk_field_kb, bulk_limit_type_kb, bulk_summary_kb,
)
from bot.states import (
    WAIT_SYMBOL, WAIT_ENTRY_AMOUNT, WAIT_STOP_LOSS, WAIT_CONFIRM,
    WAIT_STEP_PCT, WAIT_LIMIT_TYPE, WAIT_LIMIT_PRICE,
    WAIT_API_KEY, WAIT_API_SECRET, WAIT_PASSPHRASE,
    WAIT_TPL_NAME, WAIT_TPL_STEP, WAIT_TPL_LEVELS, WAIT_TPL_AMOUNT, WAIT_TPL_SL,
    WAIT_TPL_LIMIT_TYPE, WAIT_TPL_LIMIT_PCT, WAIT_TPL_TRAILING, WAIT_TPL_TRAILING_PCT,
    WAIT_EDIT_CHOICE, WAIT_EDIT_VALUE,
    WAIT_QUICK_SYMBOL,
    WAIT_GRID_LIMIT_TYPE, WAIT_GRID_LIMIT_PCT, WAIT_GRID_TRAILING, WAIT_GRID_TRAILING_PCT,
    WAIT_SESSION_EDIT_CHOICE, WAIT_SESSION_EDIT_VALUE,
    WAIT_BULK_SCOPE, WAIT_BULK_STEP, WAIT_BULK_LIMIT_TYPE, WAIT_BULK_LIMIT_PCT,
    WAIT_BULK_AMOUNT, WAIT_BULK_SL, WAIT_BULK_CONFIRM,
    WAIT_TAKE_PROFIT,
    WAIT_LEVELS_PER_SIDE,
)
from trading.grid_engine import get_engine, GridConfig
from trading.bitget_client import get_bitget, invalidate_credentials_cache
from trading.monitor import pause_rebalance, resume_rebalance
from database import db

logger = logging.getLogger(__name__)


def _authorized(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    return uid == TELEGRAM_CHAT_ID


async def _deny(update: Update) -> None:
    if update.message:
        await update.message.reply_text("⛔ غير مصرح.")
    elif update.callback_query:
        await update.callback_query.answer("⛔ غير مصرح.", show_alert=True)


async def _reply(update: Update, text: str, markup=None, parse_mode="HTML") -> None:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode=parse_mode
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)


def _session_card(session) -> str:
    cfg     = session.config
    pnl     = session.total_pnl
    pnl_pct = (pnl / cfg.entry_amount * 100) if cfg.entry_amount else 0
    icon    = "📈" if pnl >= 0 else "📉"
    depth_str = f"  •  عمق: {cfg.depth}" if cfg.depth > 0 else ""

    open_orders = [l for l in session.levels if l.status == "open"]
    buys  = sum(1 for l in open_orders if l.side == "buy")
    sells = sum(1 for l in open_orders if l.side == "sell")

    pnl_icon = "🟢" if pnl >= 0 else "🔴"
    if cfg.step_pct > 0:
        # ── Infinite-grid card ────────────────────────────────────────────────
        tp_line = f"🎯 <b>هدف الربح:</b> <code>+{cfg.profit_target}%</code>\n" if cfg.profit_target > 0 else ""
        return (
            f"♾️ <b>{cfg.symbol} (إنفنتي)</b>{depth_str}\n"
            f"<code>─────────────────────────</code>\n"
            f"📐 <b>الربح لكل غريد:</b> <code>{cfg.step_pct}%</code> (حسابي)\n"
            f"💰 <b>المبلغ لكل مستوى:</b> <code>{cfg.entry_amount:.2f} USDT</code>\n"
            f"{tp_line}"
            f"🛑 <b>وقف الخسارة:</b> <code>{f'-{cfg.stop_loss}%' if cfg.stop_loss else 'معطّل'}</code>\n"
            f"<code>─────────────────────────</code>\n"
            f"💵 <b>سعر البداية:</b> <code>{session.base_price:.6f}</code>\n"
            f"📋 <b>الأوامر:</b> 🟢 <code>{buys}</code> شراء | 🔴 <code>{sells}</code> بيع\n"
            f"<code>─────────────────────────</code>\n"
            f"{pnl_icon} <b>الربح/الخسارة:</b>\n"
            f"<b>{'+' if pnl >= 0 else ''}{pnl:.4f} USDT</b> (<b>{pnl_pct:+.2f} %</b>)\n"
            f"<code>─────────────────────────</code>\n"
            f"🆔 <b>المعرف:</b> <code>{session.id[:8]}</code>"
        )

    # ── Legacy fixed-range card ───────────────────────────────────────────────
    return (
        f"📊 <b>{cfg.symbol}</b>{depth_str}\n"
        f"<code>─────────────────────────</code>\n"
        f"💰 <b>مبلغ الدخول:</b> <code>{cfg.entry_amount:.2f} USDT</code>\n"
        f"📊 <b>عدد الشبكات:</b> <code>{cfg.grid_count}</code>\n"
        f"🛑 <b>وقف الخسارة:</b> <code>{f'-{cfg.stop_loss}%' if cfg.stop_loss else 'معطّل'}</code>\n"
        f"<code>─────────────────────────</code>\n"
        f"💵 <b>السعر الأساسي:</b> <code>{session.base_price:.6f}</code>\n"
        f"📐 <b>النطاق:</b> <code>{session.lower_price:.4f}</code> — <code>{session.upper_price:.4f}</code>\n"
        f"<code>─────────────────────────</code>\n"
        f"📋 <b>الأوامر:</b> 🟢 <code>{buys}</code> شراء | 🔴 <code>{sells}</code> بيع\n"
        f"<code>─────────────────────────</code>\n"
        f"{pnl_icon} <b>الربح/الخسارة:</b>\n"
        f"<b>{'+' if pnl >= 0 else ''}{pnl:.4f} USDT</b> (<b>{pnl_pct:+.2f} %</b>)\n"
        f"<code>─────────────────────────</code>\n"
        f"🆔 <b>المعرف:</b> <code>{session.id[:8]}</code>"
    )


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    client     = get_bitget()
    api_status = (
        "🟢 <b>مفاتيح API:</b> مُتصلة بنجاح" if await asyncio.to_thread(client.has_credentials)
        else "🔴 <b>مفاتيح API:</b> غير مُتصلة (يرجى الإعداد للبدء)"
    )
    await _reply(update,
        "✨ <b>مرحباً بك في منصة التداول الذكية</b> ✨\n"
        "<code>─────────────────────────</code>\n"
        "🤖 <b>نظام الشبكات اللانهائية (Grid Engine)</b>\n\n"
        "أنا مساعدك الآلي لإدارة استثماراتك على <b>BitGet</b>.\n"
        "يمكنك إنشاء شبكات تداول ذكية تعمل على مدار الساعة.\n\n"
        f"📊 <b>حالة الاتصال:</b>\n{api_status}\n\n"
        "👇 <b>اختر من القائمة أدناه للبدء:</b>",
        main_menu())


async def cb_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)



# ── Active grids shared helper ────────────────────────────────────────────────

def _build_display(db_rows: list[dict], engine) -> list[dict]:
    """Merge in-memory PnL into db rows list."""
    mem_ids = {s.id for s in engine.all_active()}
    display = []
    for row in db_rows:
        if row["id"] in mem_ids:
            s = engine.get_session(row["id"])
            display.append({
                "id": s.id, 
                "symbol": s.config.symbol,
                "total_pnl": s.total_pnl, 
                "depth": s.config.depth,
                "group_id": s.config.group_id,
                "entry_amount": s.config.entry_amount,
                "base_price": s.base_price,
                "levels": s.levels
            })
        else:
            display.append(row)
    return display


async def _show_grids(update: Update, engine, db_rows: list[dict],
                      filter_key: str) -> None:
    """Send/edit the active-grids message with the correct filter applied."""
    all_display = _build_display(db_rows, engine)

    if filter_key == "grouped":
        display = [r for r in all_display if r.get("group_id")]
    elif filter_key == "solo":
        display = [r for r in all_display if not r.get("group_id")]
    else:
        display = all_display

    if not display:
        labels = {"grouped": "مجمّعة", "solo": "فردية", "all": "نشطة"}
        await _reply(update,
            f"📭 لا توجد شبكات {labels.get(filter_key, '')} حالياً.",
            sessions_list([], filter_key=filter_key))
        return

    # ── FIX: Calculate REAL PnL (Realized + Unrealized) ──────────────────────
    client = get_bitget()
    try:
        prices = await asyncio.to_thread(client.get_all_tickers)
    except Exception as e:
        logger.error("Failed to fetch prices for PnL calculation: %s", e)
        prices = {}

    # Build a clear text report
    lines = [f"📊 <b>الشبكات النشطة ({len(display)} من {len(all_display)})</b>\n"]
    lines.append("<code>العملة    | الدخول   | الربح     | %</code>")
    lines.append("<code>─────────|──────────|───────────|─────</code>")
    
    for s in display[:20]:  # Cap at 20 for message length
        sym_raw = s['symbol']
        coin    = sym_raw.replace("USDT", "")
        sym     = coin.ljust(8)
        base_p  = float(s.get('base_price', 0))
        entry   = f"{base_p:.4f}".ljust(8)
        
        # Realized PnL (from closed trades)
        realized_pnl = float(s.get("total_pnl", 0))
        
        # Unrealized PnL (from current holding vs current price)
        # We estimate unrealized PnL based on open sell orders (inventory)
        unrealized_pnl = 0.0
        cur_price = prices.get(coin, 0)
        if cur_price > 0:
            levels = s.get("levels", [])
            # Sum up qty of all open SELL orders (this is our current inventory)
            inventory = sum(l.qty for l in levels if l.side == "sell" and l.status == "open")
            if inventory > 0:
                # Unrealized = inventory * (current_price - base_price)
                unrealized_pnl = inventory * (cur_price - base_p)
        
        total_pnl = realized_pnl + unrealized_pnl
        amt       = float(s.get("entry_amount", 0))
        # Total invested is roughly amt * levels_per_side
        # But for percentage, we use the same entry_amount as base
        pct       = (total_pnl / amt * 100) if amt > 0 else 0
        
        sign = "+" if total_pnl >= 0 else ""
        pnl_str = f"{sign}{total_pnl:.2f}$".ljust(9)
        pct_str = f"{sign}{pct:.1f}%"
        
        status = "🟢" if total_pnl > 0.01 else ("🔴" if total_pnl < -0.01 else "⚪️")
        lines.append(f"<code>{status} {sym}| {entry} | {pnl_str} | {pct_str}</code>")

    if len(display) > 20:
        lines.append(f"\n<i>... و {len(display)-20} شبكات أخرى</i>")

    lines.append("\n<b>اختر عملة للتحكم بها:</b>")

    await _reply(update, "\n".join(lines), sessions_list(display, filter_key=filter_key))


# ── Active grids ──────────────────────────────────────────────────────────────

async def cb_active_grids(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    engine  = get_engine()
    db_rows = await asyncio.to_thread(db.list_active_sessions)
    if not db_rows:
        await _reply(update, "📭 لا توجد شبكات نشطة حالياً.", back_main())
        return
    await _show_grids(update, engine, db_rows, filter_key="all")


async def cb_grids_grouped(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    engine  = get_engine()
    db_rows = await asyncio.to_thread(db.list_active_sessions)
    await _show_grids(update, engine, db_rows, filter_key="grouped")


async def cb_grids_solo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    engine  = get_engine()
    db_rows = await asyncio.to_thread(db.list_active_sessions)
    await _show_grids(update, engine, db_rows, filter_key="solo")


async def cb_grids_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    engine  = get_engine()
    db_rows = await asyncio.to_thread(db.list_active_sessions)
    await _show_grids(update, engine, db_rows, filter_key="all")


async def cb_grids_volatile(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show active grids sorted by volatility × volume score."""
    if not _authorized(update): return await _deny(update)
    engine  = get_engine()
    db_rows = await asyncio.to_thread(db.list_active_sessions)
    if not db_rows:
        await _reply(update, "📭 لا توجد شبكات نشطة.", back_main())
        return

    display = _build_display(db_rows, engine)
    symbols = [r["symbol"] for r in display]

    client = get_bitget()
    stats  = await asyncio.to_thread(client.get_market_stats, symbols)

    import math

    scored = []
    for r in display:
        sym = r["symbol"]
        s   = stats.get(sym)
        if not s:
            continue
        rng       = s["range24h_pct"]               # نطاق السعر 24h
        vol       = s["vol_usdt"]                   # حجم التداول بـ USDT
        chg       = s["change24h"] * 100            # تغيير % (0.032 → 3.2)
        vol_score = math.log10(vol + 1)
        score     = rng * vol_score                 # التقلب × الحجم
        scored.append((r, s, rng, chg, vol, score))

    scored.sort(key=lambda x: x[5], reverse=True)

    if not scored:
        await _reply(update, "⚠️ تعذّر جلب بيانات السوق، حاول مجدداً.", back_main())
        return

    lines = [
        "🔥 <b>فلتر التقلب العالي</b>",
        "<i>مرتبة: نطاق 24h × log(حجم USDT)</i>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for row, s, rng, chg, vol, score in scored:
        sym      = row["symbol"].replace("USDT", "")
        pnl      = float(row.get("total_pnl", 0))
        pnl_sign = "+" if pnl >= 0 else ""
        dir_icon = "🟢" if chg >= 0 else "🔴"
        vol_m    = vol / 1_000_000
        lines.append(
            f"{dir_icon} <b>{sym}</b>\n"
            f"   📊 نطاق: <code>{rng:.1f}%</code>  "
            f"│  {dir_icon} تغيير: <code>{chg:+.2f}%</code>\n"
            f"   💧 حجم: <code>{vol_m:.1f}M USDT</code>  "
            f"│  💼 P&L: <code>{pnl_sign}{pnl:.2f} USDT</code>"
        )

    await _reply(update, "\n".join(lines), sessions_list(display, filter_key="volatile"))


async def cb_session_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    sid     = update.callback_query.data.replace("session_", "")
    engine  = get_engine()
    session = engine.get_session(sid)
    if not session:
        row = await asyncio.to_thread(db.get_session, sid)
        if not row:
            await _reply(update, "❌ الشبكة غير موجودة.", back_main())
            return
        await _reply(update,
            f"📋 الشبكة <code>{sid[:8]}</code>\nالحالة: {row['status']}\nP&L: {float(row['total_pnl']):.4f} USDT",
            back_main())
        return
    await _reply(update, _session_card(session), session_actions(session.id))


async def cb_refresh_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    sid     = update.callback_query.data.replace("refresh_", "")
    engine  = get_engine()
    session = engine.get_session(sid)
    if session:
        await asyncio.to_thread(engine.refresh_session, session)
        try:
            price      = await asyncio.to_thread(get_bitget().get_price, session.config.symbol)
            price_line = f"\n\n🔄 آخر سعر: <code>{price:.6f}</code>"
        except Exception:
            price_line = ""
        await _reply(update, _session_card(session) + price_line, session_actions(session.id))
    else:
        await _reply(update, "❌ الشبكة غير نشطة.", back_main())


async def cb_close_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    sid = update.callback_query.data.replace("close_", "")
    await _reply(update,
        "⚠️ <b>هل أنت متأكد من إغلاق هذه الشبكة؟</b>\n\n"
        "سيتم إلغاء جميع الأوامر المفتوحة على BitGet وبيع العملة المتبقية بسعر السوق.",
        close_confirm(sid))


async def cb_close_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    sid = update.callback_query.data.replace("closeok_", "")
    engine  = get_engine()
    session = engine.get_session(sid)
    if not session:
        await _reply(update, "❌ الشبكة غير نشطة بالفعل.", back_main())
        return

    await update.callback_query.answer("⏳ جارٍ الإغلاق...")
    pnl = await asyncio.to_thread(engine.close_session, session, "manual", market_sell=True)

    msg = (
        f"✅ <b>تم إغلاق الشبكة بنجاح</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 المعرف: <code>{sid[:8]}</code>\n"
        f"📊 P&L النهائي: <b>{pnl:+.4f} USDT</b>\n"
        f"💹 تم بيع العملة بسعر السوق: {'✅' if session._sell_ok else '❌'}"
    )
    if not session._sell_ok:
        msg += f"\n⚠️ خطأ في البيع: <code>{session._sell_error}</code>"

    await _reply(update, msg, back_main())


async def cb_close_all_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    await _reply(update,
        "🔴 <b>إيقاف وإغلاق جميع الشبكات النشطة؟</b>\n\n"
        "1️⃣ سيتم إلغاء جميع الأوامر على BitGet.\n"
        "2️⃣ سيتم بيع جميع عملات الشبكات بسعر السوق.\n"
        "3️⃣ سيتم إغلاق جميع الجلسات في البوت.\n\n"
        "⚠️ <b>لا يمكن التراجع عن هذه العملية!</b>",
        close_all_confirm_kb())


async def cb_close_all_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "⏳ <b>جارٍ إغلاق جميع الشبكات...</b>\n"
        "يُرجى الانتظار، قد يستغرق الأمر بضع ثوانٍ.",
        parse_mode="HTML"
    )

    # Pause rebalance FIRST
    pause_rebalance()
    try:
        engine   = get_engine()
        client   = get_bitget()
        db_rows  = await asyncio.to_thread(db.list_active_sessions)

        total_pnl    = 0.0
        closed_count = 0
        closed_ids: set[str] = set()

        # 1. Get all active sessions (Memory + DB)
        active_in_mem = list(engine.all_active())
        
        # 2. Close memory sessions
        for session in active_in_mem:
            sid = session.id
            try:
                pnl = await asyncio.to_thread(db.session_total_pnl, sid)
                await asyncio.to_thread(db.close_session, sid, pnl)
                total_pnl += pnl
                closed_count += 1
            except Exception as e:
                logger.error("Close session %s failed: %s", sid[:8], e)
                pnl = 0.0
            finally:
                session.status   = "closed"
                session.total_pnl = pnl
                engine._sessions.pop(sid, None)
                closed_ids.add(sid)

        # 3. Close DB-only sessions
        for row in db_rows:
            if row["id"] not in closed_ids:
                try:
                    pnl = await asyncio.to_thread(db.session_total_pnl, row["id"])
                    await asyncio.to_thread(db.close_session, row["id"], pnl)
                    total_pnl    += pnl
                    closed_count += 1
                    closed_ids.add(row["id"])
                except Exception as e:
                    logger.error("DB close session %s: %s", row["id"][:8], e)

        # 4. Liquidate wallet
        final_sweep: dict = {"sold": [], "skipped": [], "errors": [], "cancelled_orders": 0}
        summary: dict     = {"cancelled_orders": 0, "market_sells": [], "errors": []}
        sweep_error: str  = ""

        try:
            # Notify user
            await update.callback_query.message.reply_text(
                "⏳ <b>إلغاء الأوامر وتصفية المحفظة بالكامل...</b>",
                parse_mode="HTML"
            )
            
            # Use liquidate_wallet which handles everything
            final_sweep = await asyncio.to_thread(client.liquidate_wallet)
            summary = {
                "cancelled_orders": final_sweep.get("cancelled_orders", 0),
                "market_sells":     final_sweep.get("sold", []),
                "errors":           final_sweep.get("errors", []),
            }
        except Exception as sweep_exc:
            sweep_error = str(sweep_exc)
            logger.error("liquidate_wallet failed during close-all: %s", sweep_exc)
        finally:
            resume_rebalance()

        # 3. Build result message
        lines = [f"✅ <b>تم إغلاق {closed_count} شبكة</b>\n"]
        lines.append(f"🚫 أوامر مُلغاة على BitGet: <b>{summary['cancelled_orders']}</b>")
        if summary["market_sells"]:
            lines.append("💹 بيع بسعر السوق:")
            for ms in summary["market_sells"]:
                lines.append(f"  • {_html.escape(str(ms))}")
        if final_sweep.get("skipped"):
            lines.append("\n⏭️ تم تخطي (كميات صغيرة):")
            for s in final_sweep["skipped"][:5]:
                lines.append(f"  • {_html.escape(str(s))}")
        sign = "+" if total_pnl >= 0 else ""
        lines.append(f"\n💵 إجمالي الربح/الخسارة: <b>{sign}{total_pnl:.4f} USDT</b>")
        if sweep_error:
            lines.append(f"\n⚠️ خطأ في التصفية: {_html.escape(sweep_error[:300])}")
        elif summary["errors"]:
            lines.append("\n⚠️ تنبيهات:")
            for err in summary["errors"][:3]:
                lines.append(f"  • {_html.escape(str(err))[:200]}")

        await update.callback_query.message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=back_main()
        )
    except Exception as e:
        logger.error("Global close failed: %s", e)
        await update.callback_query.message.reply_text(
            f"❌ خطأ غير متوقع: {e}", reply_markup=back_main()
        )


async def cb_liquidate_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    await _reply(update,
        "🧹 <b>تصفية المحفظة بالكامل (Spot)؟</b>\n\n"
        "سيقوم البوت بإلغاء <b>جميع</b> الأوامر المفتوحة وبيع <b>جميع</b> العملات المتاحة مقابل USDT.\n\n"
        "⚠️ <b>هذا يشمل العملات التي لا تتبع أي شبكة!</b>",
        liquidate_wallet_confirm_kb())


async def cb_liquidate_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⏳ جارٍ تصفية المحفظة...")

    client = get_bitget()
    try:
        res = await asyncio.to_thread(client.liquidate_wallet)
    except Exception as e:
        logger.error("liquidate_wallet failed: %s", e)
        await _reply(update, f"❌ فشلت التصفية: {_html.escape(str(e))}", back_main())
        return

    lines = ["✅ <b>اكتملت تصفية المحفظة</b>\n"]
    lines.append(f"🚫 أوامر مُلغاة: <b>{res['cancelled_orders']}</b>")
    if res["sold"]:
        lines.append("\n💹 تم بيع:")
        for s in res["sold"]: lines.append(f"  • {_html.escape(str(s))}")
    if res["skipped"]:
        lines.append("\n⏭️ تم تخطي (كميات صغيرة):")
        for s in res["skipped"][:10]: lines.append(f"  • {_html.escape(str(s))}")
    if res["errors"]:
        lines.append("\n⚠️ أخطاء:")
        for e in res["errors"][:5]:
            lines.append(f"  • {_html.escape(str(e))}")

    await _reply(update, "\n".join(lines), back_main())


# ── New Grid Conversation ─────────────────────────────────────────────────────

async def cb_new_grid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    client = get_bitget()
    if not await asyncio.to_thread(client.has_credentials):
        await _reply(update,
            "⚠️ <b>يجب إعداد مفاتيح API أولاً</b>\n\nاضغط 🔑 <b>إعداد API</b>.",
            main_menu())
        return ConversationHandler.END
    ctx.user_data.clear()
    await _reply(update,
        "🆕 <b>إنشاء شبكات جديدة</b>\n\n"
        "أدخل أسماء العملات مفصولة بفاصلة:\n"
        "مثال: <code>BTCUSDT, ETHUSDT, SOLUSDT</code>\n\n"
        "💡 أو أدخل عملة واحدة فقط لإنشاء شبكة واحدة.")
    return WAIT_SYMBOL


async def got_symbol_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw     = update.message.text.strip().upper()
    # Support both comma and space as separator, and Arabic comma
    parts   = [s.strip() for s in raw.replace("،", ",").replace(" ", ",").split(",") if s.strip()]
    if not parts:
        await update.message.reply_text("❌ أدخل اسم عملة واحدة على الأقل (مثال: BTCUSDT):")
        return WAIT_SYMBOL

    # Deduplicate while preserving order
    seen, unique = set(), []
    for s in parts:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    # ── FIX: validate all symbols IN PARALLEL (was sequential — 1 HTTP/symbol) ─
    client = get_bitget()

    async def _check_price(sym: str):
        try:
            price = await asyncio.to_thread(client.get_price, sym)
            return sym, price, None
        except Exception as e:
            return sym, None, e

    results = await asyncio.gather(*[_check_price(sym) for sym in unique])

    valid:   list[tuple[str, float]] = []
    invalid: list[str]               = []
    for sym, price, err in results:
        if err is None:
            valid.append((sym, price))
        else:
            invalid.append(sym)

    if not valid:
        await update.message.reply_text(
            f"❌ لم أجد أي عملة من اللي كتبتها على BitGet.\n"
            f"تحقق من الأسماء وحاول مجدداً (مثال: <code>BTCUSDT</code>):",
            parse_mode="HTML")
        return WAIT_SYMBOL

    ctx.user_data["symbols"]       = [s for s, _ in valid]
    ctx.user_data["symbol_prices"] = {s: p for s, p in valid}

    lines = ["✅ <b>العملات المُعتمدة:</b>"]
    for sym, price in valid:
        lines.append(f"  • <code>{sym}</code>  —  السعر الحالي: <code>{price:.6f}</code>")
    if invalid:
        lines.append(f"\n⚠️ لم يُعثر عليها: <code>{', '.join(invalid)}</code>")

    count  = len(valid)
    plural = "شبكة" if count == 1 else "شبكات"
    lines.append(
        f"\n<b>إجمالي:</b> {count} {plural} ستُنشأ بنفس الإعدادات\n\n"
        "الخطوة 1/4 — أدخل <b>الربح لكل غريد</b> (%):\n"
        "مثال: <code>1.0</code>  ←  كل حركة تحقق ربحاً بنسبة 1%\n"
        "💡 في شبكة إنفنتي، هذا يحدد المسافة السعرية الثابتة (حسابية)."
    )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    return WAIT_STEP_PCT


async def got_step_pct(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip().replace("%", ""))
        assert 0.1 <= val <= 50
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة بين 0.1 و 50:")
        return WAIT_STEP_PCT
    ctx.user_data["step_pct"] = val
    ctx.user_data["limit_type"] = ""
    ctx.user_data["limit_pct"] = 0.0
    ctx.user_data["lower_limit_price"] = 0.0
    ctx.user_data["upper_limit_price"] = 0.0
    ctx.user_data["trailing_stop"] = False
    ctx.user_data["trailing_pct"] = 0.0
    await update.message.reply_text(
        f"✅ نسبة المستوى: <b>{val}%</b>\n\n"
        "الخطوة 2/5 — أدخل <b>عدد المستويات</b> لكل جهة (فوق وتحت السعر الحالي):\n"
        "مثال: <code>5</code>  ←  5 شراء + 5 بيع = 10 أوامر\n"
        "💡 كلما زاد العدد = تغطية أوسع للسعر واستثمار أكبر",
        parse_mode="HTML",
    )
    return WAIT_LEVELS_PER_SIDE


async def got_levels_per_side(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = int(update.message.text.strip())
        assert 1 <= val <= 20
    except Exception:
        await update.message.reply_text("❌ أدخل عدداً صحيحاً بين 1 و 20:")
        return WAIT_LEVELS_PER_SIDE
    ctx.user_data["levels_per_side"] = val
    await update.message.reply_text(
        f"✅ عدد المستويات: <b>{val}</b> لكل جهة ({val * 2} أمر إجمالاً)\n\n"
        "الخطوة 3/5 — أدخل <b>المبلغ لكل مستوى</b> (USDT):\n"
        "مثال: <code>200</code>  ←  كل مستوى سيحافظ على قيمة 200 USDT من العملة.\n"
        "💡 هذا المبلغ سيُستخدم لشراء العملة في البداية وتوزيع أوامر الشراء.",
        parse_mode="HTML",
    )
    return WAIT_ENTRY_AMOUNT


async def got_limit_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    choice = q.data   # "limit_lower" or "limit_upper"
    ctx.user_data["limit_type"] = choice

    symbols       = ctx.user_data.get("symbols", [])
    symbol_prices = ctx.user_data.get("symbol_prices", {})
    step_pct      = ctx.user_data.get("step_pct", 1.0)

    # Show prices for context
    price_lines = "\n".join(
        f"  • <code>{s}</code>  @  <code>{symbol_prices.get(s, 0):.6f}</code>"
        for s in symbols
    )

    if choice == "limit_lower":
        direction = "🔽 <b>حد سفلي</b>"
        hint = (
            "كم % تحت السعر الحالي تريد إيقاف الشراء؟\n"
            "مثال: <code>10</code>  ←  الشبكة تتوقف عن الشراء إذا نزل السعر 10%"
        )
    else:
        direction = "🔼 <b>حد علوي</b>"
        hint = (
            "كم % فوق السعر الحالي تريد إيقاف البيع؟\n"
            "مثال: <code>10</code>  ←  الشبكة تتوقف عن البيع إذا ارتفع السعر 10%"
        )

    await q.edit_message_text(
        f"✅ اخترت: {direction}\n\n"
        f"أسعار العملات الحالية:\n{price_lines}\n\n"
        f"{hint}\n\n"
        f"أدخل النسبة % (بين 1 و 500):",
        parse_mode="HTML",
    )
    return WAIT_LIMIT_PRICE


async def got_limit_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        pct = float(update.message.text.strip().replace("%", "").replace(",", ""))
        assert 1 <= pct <= 500
    except Exception:
        await update.message.reply_text(
            "❌ أدخل نسبة بين 1 و 500 (مثال: <code>10</code>):",
            parse_mode="HTML",
        )
        return WAIT_LIMIT_PRICE

    choice        = ctx.user_data.get("limit_type", "limit_lower")
    symbols       = ctx.user_data.get("symbols", [])
    symbol_prices = ctx.user_data.get("symbol_prices", {})
    step_pct      = ctx.user_data.get("step_pct", 1.0)

    # Store the % — absolute price computed per-symbol later in _create_one
    ctx.user_data["limit_pct"]          = pct
    ctx.user_data["lower_limit_price"]  = 0.0
    ctx.user_data["upper_limit_price"]  = 0.0

    # Build per-symbol preview and estimate levels from first symbol
    est_levels    = 5
    preview_lines = []
    for sym in symbols:
        cur = symbol_prices.get(sym, 0)
        if cur <= 0:
            continue
        limit_price = cur * (1 - pct / 100) if choice == "limit_lower" else cur * (1 + pct / 100)
        step_size   = cur * step_pct / 100
        lvl         = max(1, round(abs(cur - limit_price) / step_size)) if step_size > 0 else 5
        preview_lines.append(
            f"  • <code>{sym}</code>  @  <code>{cur:.6f}</code>"
            f"  →  حد: <code>{limit_price:.6f}</code>  (~{lvl} مستوى)"
        )
        if sym == symbols[0]:
            est_levels = lvl

    ctx.user_data["levels_per_side"] = est_levels  # overwritten per-symbol in engine

    arrow  = "🔽" if choice == "limit_lower" else "🔼"
    sign   = "-"  if choice == "limit_lower" else "+"
    label  = f"{arrow} حد {'سفلي' if choice == 'limit_lower' else 'علوي'}: <b>{sign}{pct}%</b> من السعر الحالي"
    preview = "\n".join(preview_lines)

    await update.message.reply_text(
        f"✅ {label}\n{preview}\n\n"
        "الخطوة 3/5 — أدخل <b>مبلغ لكل مستوى</b> (USDT):\n"
        "الحد الأدنى يُحدَّد تلقائياً من BitGet حسب كل عملة\n"
        "مثال: <code>10</code>  ←  كل أمر شراء أو بيع قيمته 10 USDT",
        parse_mode="HTML",
    )
    return WAIT_ENTRY_AMOUNT


async def got_entry_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    from trading.bitget_client import get_bitget

    text = update.message.text.strip()
    try:
        val = float(text)
        assert val > 0
    except Exception:
        await update.message.reply_text("❌ أدخل مبلغاً موجباً بـ USDT:")
        return WAIT_ENTRY_AMOUNT

    # ── FIX: fetch min_notional for all symbols IN PARALLEL ───────────────────
    symbols  = ctx.user_data.get("symbols", [])
    client   = get_bitget()

    async def _get_min(sym: str) -> float:
        try:
            return await asyncio.to_thread(client.get_min_notional, sym)
        except Exception:
            return 1.0

    mins = await asyncio.gather(*[_get_min(sym) for sym in symbols])
    min_notional = max(mins) if mins else 1.0

    # تقريب للأعلى لأقرب رقم صحيح يسهّل على المستخدم
    min_display = max(1.0, min_notional)

    if val < min_notional:
        await update.message.reply_text(
            f"❌ الحد الأدنى لهذه العملة على BitGet هو <b>{min_display:.2f} USDT</b> للأمر الواحد.\n"
            f"أدخل قيمة لا تقل عن <code>{min_display:.2f}</code>:",
            parse_mode="HTML",
        )
        return WAIT_ENTRY_AMOUNT

    ctx.user_data["entry_amount"]    = val
    ctx.user_data["min_notional"]    = min_notional
    lvl   = ctx.user_data.get("levels_per_side", 5)
    coins = len(symbols)
    
    # ── FIX: Balance check and smart advice ──────────────────────────────────
    try:
        balance_list = await asyncio.to_thread(client.get_account_balance)
        usdt_balance = 0.0
        for b in balance_list:
            if (b.get("coin") or b.get("coinName", "")) == "USDT":
                usdt_balance = float(b.get("available", 0))
                break
    except Exception:
        usdt_balance = 0.0  # Fallback if API fails

    # Infinity Grid (Arithmetic): entry_amount is per level.
    # We place 'lvl' buy orders (locked USDT) and 'lvl' sell orders (requires market buy).
    # So total USDT needed per coin = entry_amount * levels_per_side * 2
    init_needed = val * lvl * 2 * coins   # Total needed for all coins
    
    balance_warning = ""
    if usdt_balance < init_needed:
        # Smart Advice Logic
        suggested_amt = floor(usdt_balance / (2 * coins)) if coins > 0 else 0
        suggested_lvl = floor(usdt_balance / (val * 2 * coins)) if (val * 2 * coins) > 0 else 0
        
        balance_warning = (
            f"\n\n⚠️ <b>تنبيه: الرصيد غير كافٍ!</b>\n"
            f"رصيدك الحالي: <code>{usdt_balance:.2f} USDT</code>\n"
            f"المطلوب للبدء: <code>{init_needed:.2f} USDT</code>\n\n"
            f"💡 <b>نصيحة ذكية:</b>\n"
        )
        if suggested_amt >= min_notional:
            balance_warning += f"• قلل الإجمالي لـ <code>{suggested_amt}</code> USDT.\n"
        if suggested_lvl >= 1:
            balance_warning += f"• أو قلل عدد المستويات لـ <code>{suggested_lvl}</code>.\n"
        balance_warning += "• أو قلل عدد العملات المختارة."

    await update.message.reply_text(
        f"✅ إجمالي الميزانية: <b>{val} USDT</b>\n"
        f"   (الحد الأدنى من BitGet: <code>{min_display:.2f} USDT</code>)\n"
        f"   (المبلغ المطلوب: <b>~{init_needed:.0f} USDT</b> لجميع العملات)"
        f"{balance_warning}\n\n"
        "الخطوة 4/5 — <b>هدف الربح (Take Profit) %</b>:\n"
        "إذا ارتفع سعر العملة بهذه النسبة من سعر بداية الشبكة تُغلق تلقائياً بربح\n"
        "مثال: <code>5</code>  ←  أغلق إذا ارتفع 5%\n"
        "أو <code>0</code> لتعطيله",
        parse_mode="HTML")
    return WAIT_TAKE_PROFIT


async def got_take_profit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle take profit percentage input."""
    text = update.message.text.strip().replace("%", "").lower()
    if text in ("0", "skip", "لا", "تخطي"):
        ctx.user_data["take_profit"] = 0.0
        tp_str = "❌ معطّل"
    else:
        try:
            val = float(text)
            assert val > 0
            ctx.user_data["take_profit"] = val
            tp_str = f"+{val}%"
        except Exception:
            await update.message.reply_text(
                "❌ أدخل نسبة موجبة أو <code>0</code> لتعطيله:", parse_mode="HTML")
            return WAIT_TAKE_PROFIT

    await update.message.reply_text(
        f"✅ هدف الربح: <b>{tp_str}</b>\n\n"
        "الخطوة 5/5 — <b>وقف الخسارة (Stop Loss) %</b>:\n"
        "إذا انخفض سعر العملة بهذه النسبة من سعر بداية الشبكة تُغلق تلقائياً\n"
        "مثال: <code>5</code>  ←  أغلق إذا انخفض 5%\n"
        "أو <code>0</code> لتعطيله",
        parse_mode="HTML")
    return WAIT_STOP_LOSS


async def got_stop_loss(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle stop loss — go directly to confirmation summary."""
    text = update.message.text.strip().replace("%", "").lower()
    if text in ("0", "skip", "لا", "تخطي"):
        ctx.user_data["stop_loss"] = 0.0
        sl_str = "❌ معطّل"
    else:
        try:
            val = float(text)
            assert val > 0
            ctx.user_data["stop_loss"] = val
            sl_str = f"-{val}%"
        except Exception:
            await update.message.reply_text(
                "❌ أدخل نسبة موجبة أو <code>0</code> لتعطيله:", parse_mode="HTML")
            return WAIT_STOP_LOSS

    # Proceed to summary
    from bot.manual_grid_handlers import _show_grid_summary
    return await _show_grid_summary(update, ctx)


async def _create_one(sym: str, d: dict, group_id: str):
    """Helper to create one grid session."""
    engine = get_engine()
    
    # Compute absolute limit prices from percentage if set
    cur_price = d["symbol_prices"].get(sym, 0)
    lp = 0.0
    up = 0.0
    if d.get("limit_pct", 0) > 0:
        if d.get("limit_type") == "limit_lower":
            lp = cur_price * (1 - d["limit_pct"] / 100)
        else:
            up = cur_price * (1 + d["limit_pct"] / 100)

    cfg = GridConfig(
        symbol=sym,
        entry_amount=d["entry_amount"],
        step_pct=d["step_pct"],
        levels_per_side=d["levels_per_side"],
        lower_limit_price=lp,
        upper_limit_price=up,
        profit_target=d.get("take_profit", 0.0),
        stop_loss=d["stop_loss"],
        group_id=group_id,
        trailing_stop=d.get("trailing_stop", False),
        trailing_pct=d.get("trailing_pct", 0.0),
    )
    return await asyncio.to_thread(engine.create_session, cfg)


async def cb_confirm_grid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update): return await _deny(update)
    d       = ctx.user_data
    symbols = d.get("symbols", [])
    if not symbols:
        await _reply(update, "❌ انتهت الجلسة.")
        return ConversationHandler.END

    await update.callback_query.answer("🚀 جارٍ إنشاء الشبكات...")
    await update.callback_query.edit_message_text(
        f"⏳ <b>جارٍ إرسال الأوامر لـ {len(symbols)} عملة...</b>\n"
        f"قد يستغرق هذا دقيقة، سأرسل لك تقريراً عند الانتهاء.",
        parse_mode="HTML"
    )

    group_id = str(_uuid.uuid4())
    success  = []
    failed   = []

    for sym in symbols:
        try:
            session = await _create_one(sym, d, group_id)
            success.append(session)
        except Exception as e:
            logger.error("Grid create failed for %s: %s", sym, e)
            failed.append((sym, str(e)))

    # Final report
    lines = []
    if success:
        lines.append(f"✅ <b>تم إنشاء {len(success)} شبكات بنجاح!</b>\n")
        for s in success:
            placed = getattr(s, "_placed_count", 0)
            f_cnt  = getattr(s, "_failed_count", 0)
            warn   = f"  ⚠️ {f_cnt} فشلت" if f_cnt > 0 else ""
            lines.append(f"🟢 {s.config.symbol}  🆔 <code>{s.id[:8]}</code>  |  📤 {placed} أمر{warn}")
    
    if failed:
        lines.append(f"\n❌ <b>فشل إنشاء {len(failed)} شبكة:</b>")
        for sym, err in failed:
            lines.append(f"  • {sym}: {err}")

    await update.callback_query.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_menu())
    return ConversationHandler.END


async def cb_cancel_grid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update): return await _deny(update)
    await _reply(update, "❌ تم إلغاء العملية.", main_menu())
    return ConversationHandler.END


# ── Bulk Edit ─────────────────────────────────────────────────────────────────

async def cb_bulk_edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    await _reply(update,
        "✏️ <b>تعديل جماعي للشبكات</b>\n\n"
        "اختر مجموعة الشبكات التي تريد تعديلها:",
        bulk_scope_kb())


async def cb_bulk_scope(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update): return await _deny(update)
    scope = update.callback_query.data.replace("bulk_", "")
    ctx.user_data["bulk_scope"] = scope
    ctx.user_data["bulk_edits"] = {}
    await _reply(update,
        f"✅ اخترت: <b>{scope}</b>\n\n"
        "اختر الحقول التي تريد تعديلها (يمكنك اختيار أكثر من حقل):",
        bulk_field_kb())
    return WAIT_BULK_SCOPE


async def cb_bulk_field_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update): return await _deny(update)
    field = update.callback_query.data.replace("bulk_field_", "")
    
    if field == "step":
        await _reply(update, "أدخل نسبة المسافة الجديدة (%):")
        return WAIT_BULK_STEP
    elif field == "amount":
        await _reply(update, "أدخل مبلغ المستوى الجديد (USDT):")
        return WAIT_BULK_AMOUNT
    elif field == "sl":
        await _reply(update, "أدخل نسبة وقف الخسارة الجديدة (%) أو 0 لتعطيله:")
        return WAIT_BULK_SL
    elif field == "limit":
        await _reply(update, "اختر نوع الحد السعري الجديد:", bulk_limit_type_kb())
        return WAIT_BULK_LIMIT_TYPE
    
    return WAIT_BULK_SCOPE


async def got_bulk_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip().replace("%", ""))
        assert 0.1 <= val <= 50
        ctx.user_data["bulk_edits"]["step_pct"] = val
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة بين 0.1 و 50:")
        return WAIT_BULK_STEP
    return await _show_bulk_status(update, ctx)


async def got_bulk_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip())
        assert val > 0
        ctx.user_data["bulk_edits"]["entry_amount"] = val
    except Exception:
        await update.message.reply_text("❌ أدخل مبلغاً موجباً:")
        return WAIT_BULK_AMOUNT
    return await _show_bulk_status(update, ctx)


async def got_bulk_sl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip().replace("%", ""))
        ctx.user_data["bulk_edits"]["stop_loss"] = val
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة مئوية صحيحة:")
        return WAIT_BULK_SL
    return await _show_bulk_status(update, ctx)


async def cb_bulk_limit_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.callback_query.data.replace("bulk_limit_", "")
    if choice == "skip":
        ctx.user_data["bulk_edits"]["limit_type"] = ""
        ctx.user_data["bulk_edits"]["limit_pct"] = 0.0
        return await _show_bulk_status(update, ctx)
    
    ctx.user_data["bulk_edits"]["limit_type"] = choice
    await _reply(update, "أدخل نسبة الحد السعري الجديدة (%):")
    return WAIT_BULK_LIMIT_PCT


async def got_bulk_limit_pct(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip().replace("%", ""))
        assert 1 <= val <= 500
        ctx.user_data["bulk_edits"]["limit_pct"] = val
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة بين 1 و 500:")
        return WAIT_BULK_LIMIT_PCT
    return await _show_bulk_status(update, ctx)


async def _show_bulk_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    edits = ctx.user_data["bulk_edits"]
    lines = ["📝 <b>التعديلات المختارة حتى الآن:</b>"]
    if "step_pct" in edits: lines.append(f"• المسافة: <code>{edits['step_pct']}%</code>")
    if "entry_amount" in edits: lines.append(f"• المبلغ: <code>{edits['entry_amount']} USDT</code>")
    if "stop_loss" in edits: lines.append(f"• وقف الخسارة: <code>{edits['stop_loss']}%</code>")
    if "limit_type" in edits:
        ltype = "سفلي" if edits["limit_type"] == "lower" else "علوي"
        lines.append(f"• حد {ltype}: <code>{edits['limit_pct']}%</code>")
    
    lines.append("\nهل تريد إضافة تعديلات أخرى أم تطبيق التعديلات الحالية؟")
    await _reply(update, "\n".join(lines), bulk_field_kb())
    return WAIT_BULK_SCOPE


async def cb_bulk_apply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    edits = ctx.user_data.get("bulk_edits", {})
    if not edits:
        await update.callback_query.answer("⚠️ لم تختر أي تعديلات!")
        return WAIT_BULK_SCOPE
    
    scope = ctx.user_data["bulk_scope"]
    engine = get_engine()
    all_sessions = engine.all_active()
    
    if scope == "grouped":
        targets = [s for s in all_sessions if s.config.group_id]
    elif scope == "solo":
        targets = [s for s in all_sessions if not s.config.group_id]
    else:
        targets = all_sessions
        
    if not targets:
        await _reply(update, "❌ لا توجد شبكات مطابقة لهذا النطاق.", back_main())
        return ConversationHandler.END
        
    ctx.user_data["bulk_targets"] = [s.id for s in targets]
    
    summary = [f"📊 <b>تأكيد التعديل الجماعي</b>\n", f"النطاق: <b>{scope}</b>", f"عدد الشبكات المتأثرة: <b>{len(targets)}</b>\n", "التعديلات:"]
    if "step_pct" in edits: summary.append(f"• المسافة → <code>{edits['step_pct']}%</code>")
    if "entry_amount" in edits: summary.append(f"• المبلغ → <code>{edits['entry_amount']} USDT</code>")
    if "stop_loss" in edits: summary.append(f"• وقف الخسارة → <code>{edits['stop_loss']}%</code>")
    if "limit_type" in edits:
        ltype = "سفلي" if edits["limit_type"] == "lower" else "علوي"
        summary.append(f"• حد {ltype} → <code>{edits['limit_pct']}%</code>")
        
    await _reply(update, "\n".join(summary), bulk_summary_kb())
    return WAIT_BULK_CONFIRM


async def cb_bulk_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer("⏳ جارٍ تطبيق التعديلات...")
    targets = ctx.user_data["bulk_targets"]
    edits = ctx.user_data["bulk_edits"]
    engine = get_engine()
    
    success = 0
    failed = 0
    
    for sid in targets:
        session = engine.get_session(sid)
        if not session: continue
        
        try:
            # Apply edits to config
            cfg = session.config
            if "step_pct" in edits: cfg.step_pct = edits["step_pct"]
            if "entry_amount" in edits: cfg.entry_amount = edits["entry_amount"]
            if "stop_loss" in edits: cfg.stop_loss = edits["stop_loss"]
            if "limit_type" in edits:
                # Re-calculate limit price based on current price
                cur_price = await asyncio.to_thread(get_bitget().get_price, cfg.symbol)
                if edits["limit_type"] == "lower":
                    cfg.lower_limit_price = cur_price * (1 - edits["limit_pct"] / 100)
                    cfg.upper_limit_price = 0.0
                else:
                    cfg.upper_limit_price = cur_price * (1 + edits["limit_pct"] / 100)
                    cfg.lower_limit_price = 0.0
            
            # Re-initialize session with new config
            await asyncio.to_thread(engine.refresh_session, session)
            success += 1
        except Exception as e:
            logger.error("Bulk edit failed for %s: %s", session.config.symbol, e)
            failed += 1
            
    await _reply(update,
        f"✅ <b>اكتمل التعديل الجماعي</b>\n\n"
        f"• نجح: <b>{success}</b>\n"
        f"• فشل: <b>{failed}</b>",
        back_main())
    return ConversationHandler.END


# ── Session Edit ──────────────────────────────────────────────────────────────

async def cb_session_edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    sid = update.callback_query.data.replace("sedit_", "")
    session = get_engine().get_session(sid)
    if not session:
        await _reply(update, "❌ الشبكة غير نشطة.")
        return
    await _reply(update, f"✏️ <b>تعديل الشبكة {sid[:8]}</b>", session_edit_kb(sid, session.config))


async def cb_session_edit_field(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    data = update.callback_query.data.replace("seditf_", "")
    field, sid = data.split("_")
    ctx.user_data["edit_sid"] = sid
    ctx.user_data["edit_field"] = field
    
    prompts = {
        "step": "أدخل نسبة المسافة الجديدة (%):",
        "amount": "أدخل مبلغ المستوى الجديد (USDT):",
        "levels": "أدخل عدد المستويات الجديد لكل جهة:",
        "sl": "أدخل نسبة وقف الخسارة الجديدة (%) أو 0 لتعطيله:",
        "trailing": "أدخل نسبة Trailing Stop الجديدة (%) أو 0 لتعطيله:"
    }
    await _reply(update, prompts.get(field, "أدخل القيمة الجديدة:"))
    return WAIT_SESSION_EDIT_VALUE


async def got_session_edit_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    sid = ctx.user_data["edit_sid"]
    field = ctx.user_data["edit_field"]
    val_str = update.message.text.strip().replace("%", "")
    
    session = get_engine().get_session(sid)
    if not session:
        await _reply(update, "❌ الشبكة غير نشطة.")
        return ConversationHandler.END
        
    try:
        cfg = session.config
        if field == "step":
            cfg.step_pct = float(val_str)
        elif field == "amount":
            cfg.entry_amount = float(val_str)
        elif field == "levels":
            cfg.levels_per_side = int(val_str)
        elif field == "sl":
            cfg.stop_loss = float(val_str)
        elif field == "trailing":
            val = float(val_str)
            if val <= 0:
                cfg.trailing_stop = False
                cfg.trailing_pct = 0.0
            else:
                cfg.trailing_stop = True
                cfg.trailing_pct = val
                
        # Save to DB
        db_data = {}
        if field == "step": db_data["step_pct"] = cfg.step_pct
        elif field == "amount": db_data["entry_amount"] = cfg.entry_amount
        elif field == "levels": db_data["levels_per_side"] = cfg.levels_per_side
        elif field == "sl": db_data["stop_loss"] = cfg.stop_loss
        elif field == "trailing":
            db_data["trailing_stop"] = cfg.trailing_stop
            db_data["trailing_pct"] = cfg.trailing_pct
            
        await asyncio.to_thread(db.update_session, sid, db_data)
        await asyncio.to_thread(get_engine().refresh_session, session)
        await _reply(update, "✅ تم تحديث الإعدادات بنجاح.", session_actions(sid))
    except Exception as e:
        logger.error("Session edit failed: %s", e)
        await _reply(update, f"❌ فشل التحديث: {e}", session_edit_kb(sid, session.config))
        
    return ConversationHandler.END


# ── API Setup ─────────────────────────────────────────────────────────────────

async def cb_setup_api(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update): return await _deny(update)
    await _reply(update,
        "🔑 <b>إعداد مفاتيح BitGet API</b>\n\n"
        "أدخل <b>API Key</b> الخاص بك:\n"
        "💡 تأكد من تفعيل صلاحيات (Read + Spot Trade).")
    return WAIT_API_KEY


async def got_api_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["api_key"] = update.message.text.strip()
    await update.message.reply_text("أدخل <b>API Secret</b>:")
    return WAIT_API_SECRET


async def got_api_secret(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["api_secret"] = update.message.text.strip()
    await update.message.reply_text("أدخل <b>Passphrase</b>:")
    return WAIT_PASSPHRASE


async def got_passphrase(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    key = ctx.user_data["api_key"]
    sec = ctx.user_data["api_secret"]
    pas = update.message.text.strip()

    await update.message.reply_text("⏳ جارٍ التحقق من المفاتيح...")
    
    # Temporarily set to test
    db.set_setting("bitget_api_key", key)
    db.set_setting("bitget_api_secret", sec)
    db.set_setting("bitget_passphrase", pas)
    invalidate_credentials_cache()
    
    client = get_bitget()
    ok, err = await asyncio.to_thread(client.validate_credentials)
    
    if ok:
        await update.message.reply_text("✅ تم التحقق والربط بنجاح!", reply_markup=main_menu())
    else:
        await update.message.reply_text(f"❌ فشل التحقق:\n<code>{err}</code>\n\nحاول مجدداً بالضغط على إعداد API.", parse_mode="HTML", reply_markup=main_menu())
    
    return ConversationHandler.END


# ── Handlers registration ──────────────────────────────────────────────────────

def build_application(app):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(cb_balance,   pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(cb_total_pnl, pattern="^total_pnl$"))
    
    # Active grids
    app.add_handler(CallbackQueryHandler(cb_active_grids, pattern="^active_grids$"))
    app.add_handler(CallbackQueryHandler(cb_grids_all,     pattern="^grids_all$"))
    app.add_handler(CallbackQueryHandler(cb_grids_grouped, pattern="^grids_grouped$"))
    app.add_handler(CallbackQueryHandler(cb_grids_solo,    pattern="^grids_solo$"))
    app.add_handler(CallbackQueryHandler(cb_grids_volatile, pattern="^grids_volatile$"))
    
    # Session actions
    app.add_handler(CallbackQueryHandler(cb_session_detail,  pattern="^session_"))
    app.add_handler(CallbackQueryHandler(cb_refresh_session, pattern="^refresh_"))
    app.add_handler(CallbackQueryHandler(cb_close_confirm,   pattern="^close_"))
    app.add_handler(CallbackQueryHandler(cb_close_ok,        pattern="^closeok_"))
    
    # Close all & Liquidate
    app.add_handler(CallbackQueryHandler(cb_close_all_confirm, pattern="^close_all_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_close_all_ok,      pattern="^close_all_ok$"))
    app.add_handler(CallbackQueryHandler(cb_liquidate_confirm, pattern="^liquidate_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_liquidate_ok,      pattern="^liquidate_ok$"))

    # API Setup
    api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_setup_api, pattern="^setup_api$")],
        states={
            WAIT_API_KEY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, got_api_key)],
            WAIT_API_SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_api_secret)],
            WAIT_PASSPHRASE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_passphrase)],
        },
        fallbacks=[CommandHandler("cancel", cb_cancel_grid)],
    )
    app.add_handler(api_conv)

    # New Grid Conversation
    grid_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_new_grid, pattern="^new_grid$")],
        states={
            WAIT_SYMBOL:          [MessageHandler(filters.TEXT & ~filters.COMMAND, got_symbol_text)],
            WAIT_STEP_PCT:        [MessageHandler(filters.TEXT & ~filters.COMMAND, got_step_pct)],
            WAIT_LEVELS_PER_SIDE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_levels_per_side)],
            WAIT_ENTRY_AMOUNT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, got_entry_amount)],
            WAIT_TAKE_PROFIT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, got_take_profit)],
            WAIT_STOP_LOSS:       [MessageHandler(filters.TEXT & ~filters.COMMAND, got_stop_loss)],
            WAIT_CONFIRM:         [CallbackQueryHandler(cb_confirm_grid, pattern="^confirm$"),
                                   CallbackQueryHandler(cb_cancel_grid,  pattern="^cancel$")],
        },
        fallbacks=[CommandHandler("cancel", cb_cancel_grid)],
    )
    app.add_handler(grid_conv)

    # Bulk Edit Conversation
    bulk_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_bulk_edit_start, pattern="^bulk_edit$")],
        states={
            WAIT_BULK_SCOPE:      [CallbackQueryHandler(cb_bulk_scope, pattern="^bulk_")],
            WAIT_BULK_STEP:       [MessageHandler(filters.TEXT & ~filters.COMMAND, got_bulk_step)],
            WAIT_BULK_AMOUNT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, got_bulk_amount)],
            WAIT_BULK_SL:         [MessageHandler(filters.TEXT & ~filters.COMMAND, got_bulk_sl)],
            WAIT_BULK_LIMIT_TYPE: [CallbackQueryHandler(cb_bulk_limit_type, pattern="^bulk_limit_")],
            WAIT_BULK_LIMIT_PCT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_bulk_limit_pct)],
            WAIT_BULK_CONFIRM:    [CallbackQueryHandler(cb_bulk_confirm, pattern="^bulk_confirm$")],
        },
        fallbacks=[CommandHandler("cancel", cb_cancel_grid)],
    )
    app.add_handler(bulk_conv)
    app.add_handler(CallbackQueryHandler(cb_bulk_field_choice, pattern="^bulk_field_"))

    # Session Edit Conversation
    session_edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_session_edit_start, pattern="^sedit_")],
        states={
            WAIT_SESSION_EDIT_CHOICE: [CallbackQueryHandler(cb_session_edit_field, pattern="^seditf_")],
            WAIT_SESSION_EDIT_VALUE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_session_edit_value)],
        },
        fallbacks=[CommandHandler("cancel", cb_cancel_grid)],
    )
    app.add_handler(session_edit_conv)

async def cb_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    await update.callback_query.answer("⏳ جارٍ جلب الرصيد...")
    try:
        client = get_bitget()
        balances = await asyncio.to_thread(client.get_account_balance)
        
        lines = ["🏦 <b>رصيد المحفظة (Spot)</b>\n", "━━━━━━━━━━━━━━━━━━━━"]
        usdt_val = 0.0
        for b in balances:
            coin = b.get("coin") or b.get("coinName", "")
            avail = float(b.get("available", 0))
            frozen = float(b.get("frozen", 0))
            total = avail + frozen
            if total > 0:
                if coin == "USDT": usdt_val = total
                lines.append(f"<b>{coin}:</b> <code>{total:.4f}</code> (متاح: {avail:.4f})")
        
        if len(lines) <= 2:
            lines.append("المحفظة فارغة حالياً.")
            
        await _reply(update, "\n".join(lines), back_main())
    except Exception as e:
        logger.error("Balance fetch failed: %s", e)
        await _reply(update, f"❌ فشل جلب الرصيد: {e}", back_main())


async def cb_total_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update): return await _deny(update)
    await update.callback_query.answer("⏳ جارٍ حساب الأرباح...")
    try:
        # Get all closed sessions from DB
        sql = "SELECT COALESCE(SUM(total_pnl), 0) as total FROM grid_sessions WHERE status = 'closed'"
        row = await asyncio.to_thread(db._exec, sql, fetch="one")
        closed_pnl = float(row["total"]) if row else 0.0
        
        # Get current active sessions PnL from engine
        engine = get_engine()
        active_pnl = sum(s.total_pnl for s in engine.all_active())
        
        total = closed_pnl + active_pnl
        sign = "+" if total >= 0 else ""
        
        msg = (
            f"💰 <b>سجل الأرباح الإجمالي</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 أرباح الشبكات المغلقة: <b>{closed_pnl:+.4f} USDT</b>\n"
            f"📊 أرباح الشبكات النشطة: <b>{active_pnl:+.4f} USDT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>الإجمالي الكلي: {sign}{total:.4f} USDT</b>"
        )
        await _reply(update, msg, back_main())
    except Exception as e:
        logger.error("PnL fetch failed: %s", e)
        await _reply(update, f"❌ فشل حساب الأرباح: {e}", back_main())
