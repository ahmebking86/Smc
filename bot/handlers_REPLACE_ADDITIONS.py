# ═══════════════════════════════════════════════════════════════════════════════
# إضافات handlers.py لميزة استبدال العملة
# ═══════════════════════════════════════════════════════════════════════════════

# ========== 1. عدّل الاستيرادات في أعلى الملف ==========

# في from bot.keyboards import أضف:
#     replace_asset_kb,

# في from bot.states import أضف:
#     WAIT_REPLACE_NEW_SYMBOL, WAIT_REPLACE_CONFIRM,


# ========== 2. أضف الـ handlers دي قبل دالة build_application ==========

async def cb_replace_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    pid = update.callback_query.data.replace("replace_", "")
    engine = get_engine()
    p = engine.get_portfolio(pid)
    if not p:
        await _reply(update, "❌ المحفظة غير موجودة.", back_main())
        return
    active = [a for a in p.assets if a.status == "active"]
    if not active:
        await _reply(update, "❌ لا توجد عملات نشطة في المحفظة.", portfolio_actions(pid))
        return
    ctx.user_data["replace_pid"] = pid
    await _reply(update,
        f"🔁 <b>استبدال عملة</b>\nمحفظة: <code>{pid[:8]}</code>\n\n"
        "اختر العملة التي تريد استبدالها:",
        replace_asset_kb(pid, active))


async def cb_replace_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    data = update.callback_query.data  # replace_pick_{pid}_{SYMBOL}
    # pid is UUID (36 chars with dashes), symbol is after
    rest = data.replace("replace_pick_", "", 1)
    # UUID is 36 chars: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    if len(rest) < 37:
        await _reply(update, "❌ بيانات غير صحيحة.", back_main())
        return ConversationHandler.END
    pid = rest[:36]
    old_symbol = rest[37:]  # skip the underscore
    ctx.user_data["replace_pid"] = pid
    ctx.user_data["replace_old"] = old_symbol
    await _reply(update,
        f"🔁 استبدال <b>{old_symbol.replace('USDT','')}</b>\n\n"
        "أرسل رمز العملة الجديدة:\n"
        "مثال: <code>XRPUSDT</code> أو <code>XRP</code>",
        back_main())
    return WAIT_REPLACE_NEW_SYMBOL


async def got_replace_new_symbol(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().upper()
    if not raw.endswith("USDT"):
        raw += "USDT"
    ctx.user_data["replace_new"] = raw
    old = ctx.user_data.get("replace_old", "?")
    await update.message.reply_text(
        f"🔁 تأكيد الاستبدال:\n\n"
        f"من: <b>{old.replace('USDT','')}</b>\n"
        f"إلى: <b>{raw.replace('USDT','')}</b>\n\n"
        f"سيتم بيع القديمة وشراء الجديدة بنفس القيمة تقريباً.",
        parse_mode="HTML",
        reply_markup=confirm_cancel()
    )
    return WAIT_REPLACE_CONFIRM


async def cb_replace_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    if update.callback_query.data == "cancel":
        await _reply(update, "❌ تم الإلغاء.", main_menu())
        ctx.user_data.clear()
        return ConversationHandler.END

    pid = ctx.user_data.get("replace_pid")
    old_sym = ctx.user_data.get("replace_old")
    new_sym = ctx.user_data.get("replace_new")
    if not all([pid, old_sym, new_sym]):
        await _reply(update, "❌ بيانات ناقصة.", main_menu())
        return ConversationHandler.END

    engine = get_engine()
    p = engine.get_portfolio(pid)
    if not p:
        await _reply(update, "❌ المحفظة غير موجودة.", main_menu())
        return ConversationHandler.END

    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⏳ جارٍ الاستبدال...")

    try:
        result = await asyncio.to_thread(engine.replace_asset, p, old_sym, new_sym)
    except Exception as e:
        await update.callback_query.message.reply_text(
            f"❌ فشل الاستبدال:\n<code>{_html.escape(str(e))}</code>",
            parse_mode="HTML", reply_markup=portfolio_actions(pid))
        ctx.user_data.clear()
        return ConversationHandler.END

    lines = [f"✅ <b>تم الاستبدال</b>\n"]
    for a in result.get("actions", []):
        lines.append(f"  • {a}")
    if result.get("errors"):
        lines.append("\n⚠️ أخطاء:")
        for e in result["errors"]:
            lines.append(f"  • {_html.escape(str(e)[:100])}")
    lines.append(f"\nمن <b>{old_sym.replace('USDT','')}</b> → <b>{new_sym.replace('USDT','')}</b>")
    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=portfolio_actions(pid))
    ctx.user_data.clear()
    return ConversationHandler.END


# ========== 3. في دالة build_application أضف قبل نهاية الدالة ==========

#    replace_conv = ConversationHandler(
#        entry_points=[
#            CallbackQueryHandler(cb_replace_pick, pattern=r"^replace_pick_"),
#        ],
#        states={
#            WAIT_REPLACE_NEW_SYMBOL: [
#                MessageHandler(filters.TEXT & ~filters.COMMAND, got_replace_new_symbol)
#            ],
#            WAIT_REPLACE_CONFIRM: [
#                CallbackQueryHandler(cb_replace_confirm, pattern="^(confirm|cancel)$"),
#            ],
#        },
#        fallbacks=[CommandHandler("cancel", cmd_cancel)],
#        allow_reentry=True,
#    )
#    app.add_handler(replace_conv)
#    app.add_handler(CallbackQueryHandler(cb_replace_start, pattern=r"^replace_[a-f0-9-]{36}$"))
