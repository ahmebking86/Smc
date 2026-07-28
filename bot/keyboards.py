"""All Telegram inline keyboard builders."""

from __future__ import annotations
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 إنشاء شبكة جديدة", callback_data="new_grid"),
        ],
        [
            InlineKeyboardButton("📊 الشبكات النشطة", callback_data="active_grids"),
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


def templates_menu_kb(templates: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for t in templates:
        name = t["name"]
        rows.append([
            InlineKeyboardButton(f"⚡ {name}", callback_data=f"tpl_use_{name}"),
            InlineKeyboardButton("✏️",         callback_data=f"tpl_info_{name}"),
            InlineKeyboardButton("🗑",          callback_data=f"tpl_del_{name}"),
        ])
    rows.append([InlineKeyboardButton("➕ إنشاء قالب جديد", callback_data="tpl_new")])
    rows.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def tpl_info_kb(name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚡ استخدام القالب", callback_data=f"tpl_use_{name}")],
        [InlineKeyboardButton("✏️ تعديل القالب",   callback_data=f"tpl_edit_{name}")],
        [InlineKeyboardButton("🗑 حذف القالب",     callback_data=f"tpl_del_{name}")],
        [InlineKeyboardButton("◀️ رجوع للقوالب",   callback_data="templates_menu")],
    ])


def tpl_limit_type_kb() -> InlineKeyboardMarkup:
    """Choose limit type for template."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔽 حد سفلي", callback_data="tpl_limit_lower")],
        [InlineKeyboardButton("🔼 حد علوي", callback_data="tpl_limit_upper")],
        [InlineKeyboardButton("⏭️ تخطي", callback_data="tpl_limit_skip")],
    ])


def tpl_trailing_kb() -> InlineKeyboardMarkup:
    """Enable/disable trailing stop for template."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم، فعّل", callback_data="tpl_trailing_yes")],
        [InlineKeyboardButton("❌ لا، تخطي", callback_data="tpl_trailing_no")],
    ])


def tpl_edit_menu_kb(name: str) -> InlineKeyboardMarkup:
    """Menu to choose which template field to edit."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📐 المسافة %", callback_data=f"edit_step_{name}")],
        [InlineKeyboardButton("🔢 المستويات", callback_data=f"edit_levels_{name}")],
        [InlineKeyboardButton("💰 المبلغ USDT", callback_data=f"edit_amount_{name}")],
        [InlineKeyboardButton("🛑 وقف الخسارة", callback_data=f"edit_sl_{name}")],
        [InlineKeyboardButton("🔽 الحد السعري", callback_data=f"edit_limit_{name}")],
        [InlineKeyboardButton("📍 Trailing Stop", callback_data=f"edit_trailing_{name}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="templates_menu")],
    ])



def active_grids_filter_kb(active: str = "all") -> InlineKeyboardMarkup:
    """Filter bar shown above the active grids list.

    active: "all" | "grouped" | "solo"
    """
    def _label(key: str, text: str) -> str:
        return f"✅ {text}" if active == key else text

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_label("all",     "📊 الكل"),      callback_data="grids_all"),
            InlineKeyboardButton(_label("grouped", "📦 المجمّعة"), callback_data="grids_grouped"),
            InlineKeyboardButton(_label("solo",    "🔹 الفردية"),  callback_data="grids_solo"),
        ],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
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


def sessions_list(sessions: list[dict],
                  filter_key: str = "all") -> InlineKeyboardMarkup:
    """Session list keyboard with an optional filter row prepended.

    filter_key: "all" | "grouped" | "solo"  — highlights the active tab.
    """
    def _lbl(key: str, text: str) -> str:
        return f"✅ {text}" if filter_key == key else text

    rows = [
        [
            InlineKeyboardButton(_lbl("all",      "📊 الكل"),      callback_data="grids_all"),
            InlineKeyboardButton(_lbl("grouped",  "📦 المجمّعة"), callback_data="grids_grouped"),
            InlineKeyboardButton(_lbl("solo",     "🔹 الفردية"),  callback_data="grids_solo"),
        ],
        [
            InlineKeyboardButton(
                _lbl("volatile", "🔥 تقلب عالي"),
                callback_data="grids_volatile",
            ),
        ],
    ]
    LEADERS = ["BTC","ETH","BNB","SOL","XRP","ADA","DOGE","TON","AVAX",
               "MATIC","DOT","LINK","UNI","ATOM","LTC","TRX","NEAR","APT","SUI"]
    def _rank(s):
        sym = s['symbol'].replace("USDT","")
        try:    return (LEADERS.index(sym), 0)
        except: return (len(LEADERS), 0)

    sorted_sessions = sorted(sessions, key=_rank)
    btns = []
    for s in sorted_sessions:
        pnl       = float(s.get("total_pnl", 0))
        status    = "🟢" if pnl > 0.01 else ("🔴" if pnl < -0.01 else "⚪️")
        sym       = s['symbol'].replace("USDT", "")
        
        label     = f"{status} {sym}"
        btns.append(InlineKeyboardButton(label, callback_data=f"session_{s['id']}"))
    # عمودان في كل سطر
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i+2])
    rows.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def session_actions(session_id: str, live: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث",         callback_data=f"refresh_{session_id}"),
            InlineKeyboardButton("✏️ تعديل الإعدادات", callback_data=f"sedit_{session_id}"),
        ],
        [
            InlineKeyboardButton("⏸️ إيقاف مؤقت",   callback_data=f"pause_{session_id}"),
            InlineKeyboardButton("🗑 إغلاق نهائي", callback_data=f"close_{session_id}"),
        ],
        [InlineKeyboardButton("🔙 العودة للشبكات", callback_data="active_grids")],
    ])


def session_edit_kb(session_id: str, cfg) -> InlineKeyboardMarkup:
    """Shows editable fields with their current values."""
    sl_label   = f"-{cfg.stop_loss}%" if cfg.stop_loss else "معطّل"
    trail_label = f"{cfg.trailing_pct}%" if getattr(cfg, "trailing_stop", False) else "معطّل"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📐 نسبة الدرجة: {cfg.step_pct}%",
            callback_data=f"seditf_step_{session_id}")],
        [InlineKeyboardButton(
            f"💰 مبلغ الدرجة: {cfg.entry_amount:.2f} USDT",
            callback_data=f"seditf_amount_{session_id}")],
        [InlineKeyboardButton(
            f"🔽 الحد (مستويات): {cfg.levels_per_side}",
            callback_data=f"seditf_levels_{session_id}")],
        [InlineKeyboardButton(
            f"🛑 وقف الخسارة: {sl_label}",
            callback_data=f"seditf_sl_{session_id}")],
        [InlineKeyboardButton(
            f"📍 Trailing Stop: {trail_label}",
            callback_data=f"seditf_trailing_{session_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"session_{session_id}")],
    ])


def test_sl_confirm_kb(session_id: str) -> InlineKeyboardMarkup:
    """Shown after the SL diagnostic — lets user trigger a real close or go back."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ نعم، أغلق الشبكة الآن (اختبار)",
                                 callback_data=f"testslok_{session_id}"),
        ],
        [
            InlineKeyboardButton("◀️ رجوع", callback_data=f"session_{session_id}"),
        ],
    ])


def close_confirm(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 نعم، إغلاق",  callback_data=f"closeok_{session_id}"),
            InlineKeyboardButton("❌ لا",           callback_data=f"session_{session_id}"),
        ]
    ])


POPULAR_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "TONUSDT",
]

def yes_no_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم", callback_data="yes"),
        InlineKeyboardButton("❌ لا",  callback_data="no"),
    ]])


def symbol_picker() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(POPULAR_SYMBOLS), 2):
        pair = POPULAR_SYMBOLS[i:i+2]
        rows.append([InlineKeyboardButton(s, callback_data=f"sym_{s}") for s in pair])
    rows.append([InlineKeyboardButton("✏️ أدخل زوجاً آخر يدوياً", callback_data="sym_custom")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def network_count_picker() -> InlineKeyboardMarkup:
    """Keyboard to choose how many networks to create (1–20)."""
    counts = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20]
    rows = []
    row: list[InlineKeyboardButton] = []
    for c in counts:
        row.append(InlineKeyboardButton(str(c), callback_data=f"netcount_{c}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def limit_type_kb() -> InlineKeyboardMarkup:
    """Ask the user to choose either a lower OR upper price limit."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔽 حد سفلي (أدنى سعر للشبكة)", callback_data="limit_lower"),
        ],
        [
            InlineKeyboardButton("🔼 حد علوي (أعلى سعر للشبكة)", callback_data="limit_upper"),
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")],
    ])


# ── Bulk edit keyboards ───────────────────────────────────────────────────────

def bulk_scope_kb() -> InlineKeyboardMarkup:
    """Choose which sessions to bulk-edit: all / grouped / solo."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 جميع الشبكات", callback_data="bulk_all"),
        ],
        [
            InlineKeyboardButton("📦 المجمّعة فقط", callback_data="bulk_grouped"),
            InlineKeyboardButton("🔹 الفردية فقط", callback_data="bulk_solo"),
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data="main_menu")],
    ])


def bulk_field_kb() -> InlineKeyboardMarkup:
    """Menu to choose which fields to bulk-edit."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📐 نسبة المسافة", callback_data="bulk_field_step"),
            InlineKeyboardButton("💰 مبلغ المستوى", callback_data="bulk_field_amount"),
        ],
        [
            InlineKeyboardButton("🛑 وقف الخسارة", callback_data="bulk_field_sl"),
            InlineKeyboardButton("🔽 الحد السعري", callback_data="bulk_field_limit"),
        ],
        [InlineKeyboardButton("✅ تطبيق جميع التعديلات", callback_data="bulk_apply"),
         InlineKeyboardButton("❌ إلغاء", callback_data="main_menu")],
    ])


def bulk_limit_type_kb() -> InlineKeyboardMarkup:
    """Choose limit type for bulk edit."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔽 حد سفلي", callback_data="bulk_limit_lower")],
        [InlineKeyboardButton("🔼 حد علوي", callback_data="bulk_limit_upper")],
        [InlineKeyboardButton("⏭️ تخطي (بدون حد)", callback_data="bulk_limit_skip")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="main_menu")],
    ])


def bulk_summary_kb() -> InlineKeyboardMarkup:
    """Confirm/cancel bulk edit."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأكيد التطبيق", callback_data="bulk_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="main_menu"),
        ],
    ])
