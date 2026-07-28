"""
Advanced template handlers for Grad Bot.
Handles template creation with limit prices, trailing stop, and editing.
"""

import asyncio
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler

from bot.states import (
    WAIT_TPL_NAME, WAIT_TPL_STEP, WAIT_TPL_LEVELS, WAIT_TPL_AMOUNT, WAIT_TPL_SL,
    WAIT_TPL_LIMIT_TYPE, WAIT_TPL_LIMIT_PCT, WAIT_TPL_TRAILING, WAIT_TPL_TRAILING_PCT,
    WAIT_EDIT_CHOICE, WAIT_EDIT_VALUE, WAIT_QUICK_SYMBOL, WAIT_CONFIRM
)
from bot.keyboards import (
    tpl_limit_type_kb, tpl_trailing_kb, tpl_edit_menu_kb, templates_menu_kb,
    confirm_cancel, main_menu
)
import database.db as db

logger = logging.getLogger(__name__)


# ── Template creation with advanced options ───────────────────────────────────

async def got_tpl_sl_advanced(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """After stop loss, ask about limit type."""
    text = update.message.text.strip().replace("%", "").lower()
    try:
        val = float(text)
        assert val >= 0
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة موجبة أو 0:")
        return WAIT_TPL_SL
    
    ctx.user_data["tpl_sl"] = val
    
    # Ask about limit type
    await update.message.reply_text(
        "🎯 <b>الخطوة 5/6 — هل تريد حد سعري للقالب؟</b>\n\n"
        "• <b>🔽 حد سفلي:</b> توقف الشراء تحت هذا السعر\n"
        "• <b>🔼 حد علوي:</b> توقف البيع فوق هذا السعر\n"
        "• <b>⏭️ تخطي:</b> شبكة لا نهائية بدون حد سعري",
        reply_markup=tpl_limit_type_kb(),
        parse_mode="HTML",
    )
    return WAIT_TPL_LIMIT_TYPE


async def got_tpl_limit_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle limit type selection."""
    q = update.callback_query
    await q.answer()
    choice = q.data
    
    if choice == "tpl_limit_skip":
        ctx.user_data["tpl_limit_type"] = ""
        ctx.user_data["tpl_limit_pct"] = 0
        # Skip to trailing stop
        await q.edit_message_text(
            "⏭️ <b>تم تخطي الحد السعري</b>\n\n"
            "🎯 <b>الخطوة 6/6 — Trailing Stop</b>\n\n"
            "هل تريد تفعيل Trailing Stop؟\n"
            "يتابع السعر تلقائياً ويبيع عند انخفاض معين.",
            reply_markup=tpl_trailing_kb(),
            parse_mode="HTML",
        )
        return WAIT_TPL_TRAILING
    
    ctx.user_data["tpl_limit_type"] = choice
    direction = "🔽 حد سفلي" if choice == "tpl_limit_lower" else "🔼 حد علوي"
    
    await q.edit_message_text(
        f"✅ اخترت: {direction}\n\n"
        f"أدخل النسبة % (بين 1 و 500):\n"
        f"مثال: <code>10</code>",
        parse_mode="HTML",
    )
    return WAIT_TPL_LIMIT_PCT


async def got_tpl_limit_pct(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Store limit percentage."""
    try:
        pct = float(update.message.text.strip().replace("%", ""))
        assert 1 <= pct <= 500
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة بين 1 و 500:")
        return WAIT_TPL_LIMIT_PCT
    
    ctx.user_data["tpl_limit_pct"] = pct
    
    # Ask about trailing stop
    await update.message.reply_text(
        f"✅ الحد السعري: <code>{pct}%</code>\n\n"
        f"🎯 <b>الخطوة 6/6 — Trailing Stop</b>\n\n"
        f"هل تريد تفعيل Trailing Stop؟\n"
        f"يتابع السعر تلقائياً ويبيع عند انخفاض معين.",
        reply_markup=tpl_trailing_kb(),
        parse_mode="HTML",
    )
    return WAIT_TPL_TRAILING


async def got_tpl_trailing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle trailing stop choice."""
    q = update.callback_query
    await q.answer()
    choice = q.data
    
    if choice == "tpl_trailing_no":
        ctx.user_data["tpl_trailing_stop"] = False
        ctx.user_data["tpl_trailing_pct"] = 0
        # Save template
        return await _save_template(update, ctx)
    
    ctx.user_data["tpl_trailing_stop"] = True
    await q.edit_message_text(
        "✅ تم تفعيل Trailing Stop\n\n"
        "أدخل النسبة % (مثال: <code>2</code>):\n"
        "عندما ينخفض السعر بهذه النسبة، يتم البيع تلقائياً.",
        parse_mode="HTML",
    )
    return WAIT_TPL_TRAILING_PCT


async def got_tpl_trailing_pct(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Store trailing percentage and save template."""
    try:
        pct = float(update.message.text.strip().replace("%", ""))
        assert 0.1 <= pct <= 50
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة بين 0.1 و 50:")
        return WAIT_TPL_TRAILING_PCT
    
    ctx.user_data["tpl_trailing_pct"] = pct
    return await _save_template(update, ctx)


async def _save_template(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Save template with all fields."""
    d = ctx.user_data
    cfg = {
        "step_pct":       d.get("tpl_step", 1.0),
        "levels_per_side": d.get("tpl_levels", 5),
        "entry_amount":   d.get("tpl_amount", 2.0),
        "stop_loss":      d.get("tpl_sl", 0.0),
        "limit_type":     d.get("tpl_limit_type", ""),
        "limit_pct":      d.get("tpl_limit_pct", 0),
        "trailing_stop":  d.get("tpl_trailing_stop", False),
        "trailing_pct":   d.get("tpl_trailing_pct", 0),
    }
    
    await asyncio.to_thread(db.save_template, d["tpl_name"], cfg)
    
    # Show confirmation
    sl_str = f"-{cfg['stop_loss']}%" if cfg['stop_loss'] > 0 else "❌ معطّل"
    limit_str = ""
    if cfg["limit_type"]:
        direction = "🔽 سفلي" if cfg["limit_type"] == "tpl_limit_lower" else "🔼 علوي"
        limit_str = f"\n🔽 الحد السعري: {direction} <code>{cfg['limit_pct']}%</code>"
    
    trailing_str = ""
    if cfg["trailing_stop"]:
        trailing_str = f"\n📍 Trailing Stop: <code>{cfg['trailing_pct']}%</code>"
    
    templates = await asyncio.to_thread(db.get_templates)
    await update.message.reply_text(
        f"✅ <b>تم حفظ القالب: {d['tpl_name']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📐 المسافة:      <code>{cfg['step_pct']}%</code>\n"
        f"🔢 المستويات:     <code>{cfg['levels_per_side']}</code> (لكل جهة)\n"
        f"💰 مبلغ المستوى:  <code>{cfg['entry_amount']} USDT</code>\n"
        f"🛑 وقف الخسارة:   <code>{sl_str}</code>"
        f"{limit_str}"
        f"{trailing_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 استثمار/عملة: <b>~{cfg['entry_amount'] * cfg['levels_per_side']:.0f} USDT</b>",
        reply_markup=templates_menu_kb(templates),
        parse_mode="HTML",
    )
    ctx.user_data.clear()
    return ConversationHandler.END


# ── Template editing ──────────────────────────────────────────────────────────

async def cb_tpl_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Start template edit."""
    if not hasattr(update, 'callback_query'):
        return ConversationHandler.END
    
    q = update.callback_query
    await q.answer()
    name = q.data.replace("tpl_edit_", "")
    
    templates = await asyncio.to_thread(db.get_templates)
    t = next((x for x in templates if x["name"] == name), None)
    if not t:
        await q.edit_message_text("❌ القالب غير موجود.")
        return ConversationHandler.END
    
    ctx.user_data.clear()
    ctx.user_data["tpl_name"] = name
    ctx.user_data["tpl_original"] = t.copy()
    
    await q.edit_message_text(
        f"✏️ <b>تعديل القالب: {name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"اختر الحقل المراد تعديله:",
        reply_markup=tpl_edit_menu_kb(name),
        parse_mode="HTML",
    )
    return WAIT_EDIT_CHOICE


async def got_edit_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle edit field selection."""
    q = update.callback_query
    await q.answer()
    data = q.data
    
    if data.startswith("edit_step_"):
        ctx.user_data["edit_field"] = "step_pct"
        await q.edit_message_text(
            "📐 أدخل المسافة الجديدة بين المستويات (%):\n"
            f"القيمة الحالية: <code>{ctx.user_data['tpl_original']['step_pct']}%</code>",
            parse_mode="HTML",
        )
    elif data.startswith("edit_levels_"):
        ctx.user_data["edit_field"] = "levels_per_side"
        await q.edit_message_text(
            "🔢 أدخل عدد المستويات الجديد:\n"
            f"القيمة الحالية: <code>{ctx.user_data['tpl_original']['levels_per_side']}</code>",
            parse_mode="HTML",
        )
    elif data.startswith("edit_amount_"):
        ctx.user_data["edit_field"] = "entry_amount"
        await q.edit_message_text(
            "💰 أدخل المبلغ الجديد (USDT):\n"
            f"القيمة الحالية: <code>{ctx.user_data['tpl_original']['entry_amount']} USDT</code>",
            parse_mode="HTML",
        )
    elif data.startswith("edit_sl_"):
        ctx.user_data["edit_field"] = "stop_loss"
        await q.edit_message_text(
            "🛑 أدخل نسبة وقف الخسارة الجديدة (%):\n"
            f"القيمة الحالية: <code>{ctx.user_data['tpl_original'].get('stop_loss', 0)}%</code>",
            parse_mode="HTML",
        )
    else:
        await q.edit_message_text("❌ خيار غير معروف.")
        return ConversationHandler.END
    
    return WAIT_EDIT_VALUE


async def got_edit_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Save edited value."""
    field = ctx.user_data.get("edit_field")
    text = update.message.text.strip().replace("%", "")
    
    try:
        if field == "step_pct":
            val = float(text)
            assert 0.1 <= val <= 50
        elif field == "levels_per_side":
            val = int(text)
            assert 1 <= val <= 50
        elif field == "entry_amount":
            val = float(text)
            assert val > 0
        elif field == "stop_loss":
            val = float(text)
            assert val >= 0
        else:
            val = float(text)
    except Exception:
        await update.message.reply_text("❌ قيمة غير صحيحة. حاول مرة أخرى:")
        return WAIT_EDIT_VALUE
    
    # Update template
    name = ctx.user_data["tpl_name"]
    original = ctx.user_data["tpl_original"]
    original[field] = val
    
    await asyncio.to_thread(db.save_template, name, original)
    
    templates = await asyncio.to_thread(db.get_templates)
    await update.message.reply_text(
        f"✅ <b>تم تحديث القالب: {name}</b>\n"
        f"✏️ {field}: <code>{val}</code>",
        reply_markup=templates_menu_kb(templates),
        parse_mode="HTML",
    )
    ctx.user_data.clear()
    return ConversationHandler.END
