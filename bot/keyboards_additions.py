"""
إضافات لوحة المفاتيح — الأزرار الجديدة
انسخ المحتوى ده داخل bot/keyboards.py أو استورد منه.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def portfolio_actions_v2(portfolio_id: str) -> InlineKeyboardMarkup:
    """نسخة محسّنة من أزرار المحفظة مع الميزات الجديدة."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث / إعادة توازن الآن", callback_data=f"rebalance_now_{portfolio_id}"),
        ],
        [
            InlineKeyboardButton("🔁 استبدال عملة", callback_data=f"replace_{portfolio_id}"),
            InlineKeyboardButton("🗑 حذف عملة", callback_data=f"delete_asset_{portfolio_id}"),
        ],
        [
            InlineKeyboardButton("➕ زيادة استثمار", callback_data=f"add_funds_{portfolio_id}"),
            InlineKeyboardButton("➖ تخفيف استثمار", callback_data=f"reduce_funds_{portfolio_id}"),
        ],
        [
            InlineKeyboardButton("📈 تقرير أفضل أداء", callback_data=f"performance_{portfolio_id}"),
        ],
        [
            InlineKeyboardButton("📊 التفاصيل", callback_data=f"portfolio_{portfolio_id}"),
            InlineKeyboardButton("⏸️ إيقاف مؤقت", callback_data=f"pause_{portfolio_id}"),
        ],
        [
            InlineKeyboardButton("🗑 إغلاق المحفظة", callback_data=f"close_{portfolio_id}"),
        ],
        [InlineKeyboardButton("🔙 العودة للمحافظ", callback_data="active_portfolios")],
    ])


def delete_asset_kb(portfolio_id: str, assets: list) -> InlineKeyboardMarkup:
    """أزرار اختيار العملة المراد حذفها."""
    rows = []
    for a in assets:
        coin = a.symbol.replace("USDT", "") if hasattr(a, "symbol") else str(a).replace("USDT", "")
        symbol = a.symbol if hasattr(a, "symbol") else a
        rows.append([
            InlineKeyboardButton(
                f"🗑 حذف {coin}",
                callback_data=f"delete_pick_{portfolio_id}_{symbol}"
            )
        ])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"portfolio_{portfolio_id}")])
    return InlineKeyboardMarkup(rows)


def confirm_delete_kb(portfolio_id: str, symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نعم، احذف وبيع", callback_data=f"delete_ok_{portfolio_id}_{symbol}"),
            InlineKeyboardButton("❌ لا", callback_data=f"portfolio_{portfolio_id}"),
        ]
    ])
