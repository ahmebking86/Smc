"""
Advanced handlers for manual grid creation with limit prices and trailing stop.
"""

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import (
    WAIT_STOP_LOSS,
    WAIT_GRID_LIMIT_TYPE, WAIT_GRID_LIMIT_PCT,
    WAIT_GRID_TRAILING, WAIT_GRID_TRAILING_PCT,
    WAIT_CONFIRM,
)
from bot.keyboards import tpl_limit_type_kb, tpl_trailing_kb, confirm_cancel


async def got_stop_loss_advanced(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """After stop loss, ask about limit type."""
    text = update.message.text.strip().replace("%", "").lower()
    if text in ("0", "skip", "لا", "تخطي"):
        ctx.user_data["stop_loss"] = 0.0
        label = "❌ معطّل"
    else:
        try:
            val = float(text)
            assert val > 0
            ctx.user_data["stop_loss"] = val
            label = f"-{val}%"
        except Exception:
            await update.message.reply_text("❌ أدخل نسبة موجبة أو <code>0</code> لتعطيله:", parse_mode="HTML")
            return WAIT_STOP_LOSS
    
    # Ask about limit type
    await update.message.reply_text(
        "🎯 <b>الخطوة 5/7 — الحدود السعرية</b>\n\n"
        "• <b>🔽 حد سفلي:</b> توقف الشراء تحت هذا السعر\n"
        "• <b>🔼 حد علوي:</b> توقف البيع فوق هذا السعر\n"
        "• <b>⏭️ تخطي:</b> شبكة لا نهائية بدون حد سعري",
        reply_markup=tpl_limit_type_kb(),
        parse_mode="HTML",
    )
    return WAIT_GRID_LIMIT_TYPE


async def got_grid_limit_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle limit type selection for manual grid."""
    q = update.callback_query
    await q.answer()
    choice = q.data
    
    if choice == "tpl_limit_skip":
        ctx.user_data["limit_type"] = ""
        ctx.user_data["limit_pct"] = 0
        # Skip to trailing stop
        await q.edit_message_text(
            "⏭️ <b>تم تخطي الحد السعري</b>\n\n"
            "🎯 <b>الخطوة 6/7 — Trailing Stop</b>\n\n"
            "هل تريد تفعيل Trailing Stop؟\n"
            "يتابع السعر تلقائياً ويبيع عند انخفاض معين.",
            reply_markup=tpl_trailing_kb(),
            parse_mode="HTML",
        )
        return WAIT_GRID_TRAILING
    
    ctx.user_data["limit_type"] = choice
    direction = "🔽 حد سفلي" if choice == "tpl_limit_lower" else "🔼 حد علوي"
    
    await q.edit_message_text(
        f"✅ اخترت: {direction}\n\n"
        f"أدخل النسبة % (بين 1 و 500):\n"
        f"مثال: <code>10</code>",
        parse_mode="HTML",
    )
    return WAIT_GRID_LIMIT_PCT


async def got_grid_limit_pct(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Store limit percentage for manual grid."""
    try:
        pct = float(update.message.text.strip().replace("%", ""))
        assert 1 <= pct <= 500
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة بين 1 و 500:")
        return WAIT_GRID_LIMIT_PCT
    
    ctx.user_data["limit_pct"] = pct
    
    # Ask about trailing stop
    await update.message.reply_text(
        f"✅ الحد السعري: <code>{pct}%</code>\n\n"
        f"🎯 <b>الخطوة 6/7 — Trailing Stop</b>\n\n"
        f"هل تريد تفعيل Trailing Stop؟\n"
        f"يتابع السعر تلقائياً ويبيع عند انخفاض معين.",
        reply_markup=tpl_trailing_kb(),
        parse_mode="HTML",
    )
    return WAIT_GRID_TRAILING


async def got_grid_trailing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle trailing stop choice for manual grid."""
    q = update.callback_query
    await q.answer()
    choice = q.data
    
    if choice == "tpl_trailing_no":
        ctx.user_data["trailing_stop"] = False
        ctx.user_data["trailing_pct"] = 0
        # Proceed to summary
        return await _show_grid_summary(update, ctx)
    
    ctx.user_data["trailing_stop"] = True
    await q.edit_message_text(
        "✅ تم تفعيل Trailing Stop\n\n"
        "أدخل النسبة % (مثال: <code>2</code>):\n"
        "عندما ينخفض السعر بهذه النسبة، يتم البيع تلقائياً.",
        parse_mode="HTML",
    )
    return WAIT_GRID_TRAILING_PCT


async def got_grid_trailing_pct(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Store trailing percentage and show summary."""
    try:
        pct = float(update.message.text.strip().replace("%", ""))
        assert 0.1 <= pct <= 50
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة بين 0.1 و 50:")
        return WAIT_GRID_TRAILING_PCT
    
    ctx.user_data["trailing_pct"] = pct
    return await _show_grid_summary(update, ctx)


async def _show_grid_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Show final summary before confirming grid creation."""
    d       = ctx.user_data
    symbols = d.get("symbols", [])
    if not symbols:
        await update.message.reply_text("❌ انتهت الجلسة، ابدأ من جديد.")
        return ConversationHandler.END
    
    count   = len(symbols)
    plural  = "شبكة" if count == 1 else "شبكات"
    prices  = d.get("symbol_prices", {})
    lvl     = d.get("levels_per_side", 5)
    step    = d.get("step_pct", 1.0)
    amt     = d.get("entry_amount", 0.0)
    sl      = d.get("stop_loss", 0.0)
    limit_pct     = d.get("limit_pct", 0.0)
    limit_type    = d.get("limit_type", "")
    trailing_stop = d.get("trailing_stop", False)
    trailing_pct  = d.get("trailing_pct", 0.0)
    
    init_per_coin = amt * lvl
    total         = init_per_coin * count
    sl_str        = f"-{sl}%" if sl > 0 else "❌ معطّل"

    # Build limit string
    if limit_pct > 0 and limit_type == "tpl_limit_lower":
        limit_str = f"🔽 حد سفلي: <b>-{limit_pct}%</b> من السعر الحالي"
    elif limit_pct > 0 and limit_type == "tpl_limit_upper":
        limit_str = f"🔼 حد علوي: <b>+{limit_pct}%</b> من السعر الحالي"
    else:
        limit_str = f"♾️ بدون حد سعري — تتمدد تلقائياً"

    # Build trailing stop string
    trailing_str = ""
    if trailing_stop:
        trailing_str = f"\n📍 Trailing Stop: <code>{trailing_pct}%</code>"

    sym_lines = []
    for sym in symbols:
        p = prices.get(sym, 0)
        sym_lines.append(f"  • <code>{sym}</code>  @  <code>{p:.6f}</code>")

    msg = (
        f"📋 <b>ملخص — {count} {plural} إنفنتي (BingX Spec)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>العملات:</b>\n" + "\n".join(sym_lines) + "\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>الإعدادات:</b>\n"
        f"📐 الربح لكل غريد: <code>{step}%</code> (حسابي)\n"
        f"{limit_str}\n"
        f"💰 مبلغ كل مستوى:  <code>{amt} USDT</code>\n"
        f"🛑 وقف الخسارة:    <code>{sl_str}</code>"
        f"{trailing_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"♾️  شبكة إنفنتي فوري — تحافظ على قيمة الأصول\n"
        f"💵 استثمار ابتدائي تقريبي: <b>~{total:.0f} USDT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ ستُرسل الأوامر الآن لـ <b>BitGet</b> فعلياً.\n"
        f"هل تريد تأكيد إنشاء <b>{count}</b> {plural}؟"
    )
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=confirm_cancel(), parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(msg, reply_markup=confirm_cancel(), parse_mode="HTML")
    
    return WAIT_CONFIRM

