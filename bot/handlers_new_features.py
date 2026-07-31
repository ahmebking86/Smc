"""
Handlers جديدة — انسخها داخل bot/handlers.py وسجّلها في الـ application.
"""

# مثال مبسّط للـ handlers (يحتاج تكملة مع الداتابيز والـ engine)

async def cb_delete_asset_start(update, ctx):
    """يبدأ عملية حذف عملة."""
    q = update.callback_query
    portfolio_id = q.data.replace("delete_asset_", "")
    # جيب الأصول من الـ engine أو الداتابيز
    # assets = ...
    # await q.edit_message_text("اختر العملة المراد حذفها:", reply_markup=delete_asset_kb(portfolio_id, assets))
    await q.answer("قريبًا...")


async def cb_add_funds_start(update, ctx):
    """يبدأ زيادة الاستثمار."""
    q = update.callback_query
    portfolio_id = q.data.replace("add_funds_", "")
    ctx.user_data["add_funds_pid"] = portfolio_id
    await q.edit_message_text("أرسل المبلغ بالـ USDT الذي تريد إضافته:")
    # return WAIT_ADD_FUNDS_AMOUNT


async def cb_reduce_funds_start(update, ctx):
    """يبدأ تخفيف الاستثمار."""
    q = update.callback_query
    portfolio_id = q.data.replace("reduce_funds_", "")
    ctx.user_data["reduce_funds_pid"] = portfolio_id
    await q.edit_message_text("أرسل النسبة المئوية التي تريد تخفيفها (مثال: 20):")
    # return WAIT_REDUCE_FUNDS_AMOUNT


async def cb_performance(update, ctx):
    """يعرض تقرير أفضل أداء."""
    q = update.callback_query
    portfolio_id = q.data.replace("performance_", "")
    engine = get_engine()
    portfolio = engine._portfolios.get(portfolio_id)  # أو من الداتابيز
    if not portfolio:
        await q.answer("المحفظة غير موجودة", show_alert=True)
        return
    report = engine.performance_report(portfolio)
    await q.edit_message_text(report, parse_mode="HTML", reply_markup=portfolio_actions_v2(portfolio_id))
