"""All Telegram inline keyboard builders."""

from __future__ import annotations
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🆕 إنشاء محفظة إعادة توازن", callback_data="new_portfolio"),
        ],
        [
            InlineKeyboardButton("📊 المحافظ النشطة", callback_data="active_portfolios"),
        ],
        [
            InlineKeyboardButton("💰 سجل الأرباح", callback_data="total_pnl"),
            InlineKeyboardButton("🏦 رصيد المحفظة", callback_data="balance"),
        ],
        [
            InlineKeyboardButton("⚙️ إعدادات API", callback_data="setup_api"),
        ],
        [
            InlineKeyboardButton("🛑 إيقاف الكل",   callback_data="close_all_confirm"),
            InlineKeyboardButton("🧹 تصفية USDT",   callback_data="liquidate_confirm"),
        ],
    ])


def confirm_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد",  callback_data="confirm"),
            InlineKeyboardButton("❌ إلغاء",  callback_data="cancel"),
        ]
    ])


def back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")]
    ])


def close_all_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 نعم، أغلق الكل",  callback_data="close_all_ok"),
            InlineKeyboardButton("❌ لا، تراجع",       callback_data="main_menu"),
        ]
    ])


def liquidate_wallet_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 نعم، بيع الكل → USDT", callback_data="liquidate_ok"),
            InlineKeyboardButton("❌ إلغاء",                 callback_data="main_menu"),
        ]
    ])


def rebalance_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏰ بالوقت (كل X ساعة)", callback_data="mode_time"),
        ],
        [
            InlineKeyboardButton("📐 بالنسبة % (عند الانحراف)", callback_data="mode_percent"),
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")],
    ])


def portfolios_list(portfolios: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for p in portfolios:
        status = "🟢" if p.get("status") == "active" else "⏸️"
        label  = f"{status} محفظة {p['id'][:8]} — {p.get('asset_count', 0)} عملة"
        rows.append([InlineKeyboardButton(label, callback_data=f"portfolio_{p['id']}")])
    rows.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def portfolio_actions(portfolio_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث / إعادة توازن الآن", callback_data=f"rebalance_now_{portfolio_id}"),
        ],
        [
            InlineKeyboardButton("🔁 استبدال عملة", callback_data=f"replace_{portfolio_id}"),
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


def close_confirm(portfolio_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 نعم، إغلاق وبيع الكل", callback_data=f"closeok_{portfolio_id}"),
            InlineKeyboardButton("❌ لا",                   callback_data=f"portfolio_{portfolio_id}"),
        ]
    ])


def asset_close_kb(portfolio_id: str, symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🗑 بيع {symbol.replace('USDT','')} فقط",
                                 callback_data=f"close_asset_{portfolio_id}_{symbol}"),
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"portfolio_{portfolio_id}")],
    ])


def replace_asset_kb(portfolio_id: str, assets: list) -> InlineKeyboardMarkup:
    """أزرار اختيار العملة المراد استبدالها."""
    rows = []
    for a in assets:
        coin = a.symbol.replace("USDT", "")
        rows.append([
            InlineKeyboardButton(
                f"🔁 استبدال {coin}",
                callback_data=f"replace_pick_{portfolio_id}_{a.symbol}"
            )
        ])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"portfolio_{portfolio_id}")])
    return InlineKeyboardMarkup(rows)
