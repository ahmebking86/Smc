"""telegram_bot.py — Telegram control panel + alert sender."""
from __future__ import annotations

import asyncio
import html
import logging
import math
import threading
from typing import Optional

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters,
)
from telegram.error import TelegramError

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from database import (
    get_settings, set_trading_enabled, set_risk_percent,
    set_trade_amount, set_timeframe,
    set_active_pairs, get_active_pairs, get_open_trades, get_today_stats,
)

logger = logging.getLogger(__name__)

# ── Event loop for the bot thread ─────────────────────────────────────────────
_bot_loop: Optional[asyncio.AbstractEventLoop] = None

# ── Per-user conversation state ───────────────────────────────────────────────
_waiting_for: dict[int, str] = {}

# ── Singleton bot for sending alerts ─────────────────────────────────────────
_bot: Optional[Bot] = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


def send_alert(text: str) -> None:
    """Send a message to the admin chat from any thread."""
    async def _send():
        await get_bot().send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="HTML",
        )

    try:
        if _bot_loop and _bot_loop.is_running():
            asyncio.run_coroutine_threadsafe(_send(), _bot_loop)
        else:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_send())
            loop.close()
    except TelegramError as exc:
        logger.error("Telegram send failed: %s", exc)
    except Exception as exc:
        logger.error("send_alert error: %s", exc)


# ── Auth guard ────────────────────────────────────────────────────────────────

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user and update.effective_user.id != TELEGRAM_CHAT_ID:
            await update.effective_message.reply_text("⛔ Unauthorized")
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


# ── Symbol normaliser ─────────────────────────────────────────────────────────

def normalize_symbol(raw: str) -> str:
    """Strip ccxt perpetual suffix: BTC/USDT:USDT → BTC/USDT."""
    s = raw.strip().upper()
    if ":" in s:
        s = s.split(":")[0]
    return s


# ── Paginated pairs keyboard ──────────────────────────────────────────────────
PAIRS_PAGE_SIZE = 8   # pairs per page


def _pairs_keyboard(pairs: list[str], page: int = 0) -> InlineKeyboardMarkup:
    """
    Paginated inline keyboard for active pairs.
    Each pair row: [✅ SYMBOL]  [❌ Remove]
    Bottom row:    [◀ Prev]  [Page N/M]  [Next ▶]  + [🔙 Menu]
    """
    total_pages = max(1, math.ceil(len(pairs) / PAIRS_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    start = page * PAIRS_PAGE_SIZE
    page_pairs = pairs[start: start + PAIRS_PAGE_SIZE]

    rows = []
    for p in page_pairs:
        rows.append([
            InlineKeyboardButton(f"✅ {p}", callback_data="noop"),
            InlineKeyboardButton("❌ Remove", callback_data=f"rm_pair:{p}:{page}"),
        ])

    # Pagination row
    nav: list[InlineKeyboardButton] = []
    if total_pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"pairs_pg:{page-1}"))
        nav.append(InlineKeyboardButton(
            f"{page + 1}/{total_pages}", callback_data="noop"
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("Next ▶", callback_data=f"pairs_pg:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def _pairs_message(pairs: list[str], page: int = 0) -> str:
    total_pages = max(1, math.ceil(len(pairs) / PAIRS_PAGE_SIZE))
    return (
        f"📋 <b>Active Pairs</b> — {len(pairs)} total"
        + (f" (page {page+1}/{total_pages})" if total_pages > 1 else "")
        + "\n\nTap ❌ to remove a pair.\n"
        + "Add new: <code>/addpair SOL/USDT</code>\n"
        + "Load top: <code>/loadtop 20</code>"
    )


# ── Helper: build main menu keyboard ─────────────────────────────────────────

def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Status",        callback_data="status"),
            InlineKeyboardButton("⚡ Toggle",        callback_data="toggle"),
        ],
        [
            InlineKeyboardButton("💵 Set Amount",    callback_data="set_amount"),
            InlineKeyboardButton("⏱ Timeframe",     callback_data="set_tf_menu"),
        ],
        [
            InlineKeyboardButton("💰 Balance",       callback_data="balance"),
            InlineKeyboardButton("📋 My Pairs",      callback_data="pairs"),
        ],
        [
            InlineKeyboardButton("➕ Add Bulk",       callback_data="add_bulk"),
            InlineKeyboardButton("🌐 Load Top",       callback_data="load_top_menu"),
        ],
        [
            InlineKeyboardButton("🚨 Close ALL",     callback_data="closeall"),
        ],
    ])


# ── /start  /menu ─────────────────────────────────────────────────────────────

@admin_only
async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_settings()
    trades = get_open_trades()
    stats = get_today_stats()
    mode = "🟢 Active" if cfg.trading_enabled else "🔴 Paused"

    try:
        from trading.executor import fetch_usdt_balance
        balance = fetch_usdt_balance()
        balance_str = f"${balance:.2f}"
    except Exception:
        balance_str = "N/A"

    amount_str = (
        f"${cfg.trade_amount_usdt:.2f} USDT (fixed)"
        if cfg.trade_amount_usdt and cfg.trade_amount_usdt > 0
        else f"{cfg.risk_percent}% risk"
    )

    pairs = get_active_pairs()
    pairs_str = f"{len(pairs)} pairs" if len(pairs) > 5 else ", ".join(pairs) if pairs else "None"

    text = (
        f"🤖 <b>SMC Bitget Bot — Spot Only</b>\n\n"
        f"Status:       <b>{mode}</b>\n"
        f"Balance:      <b>{balance_str} USDT</b>\n"
        f"Amount/Trade: <b>{amount_str}</b>\n"
        f"Timeframe:    <b>{cfg.timeframe or '15m'}</b>\n"
        f"Pairs:        <b>{html.escape(pairs_str)}</b>\n\n"
        f"Open trades:  <b>{len(trades)}</b>\n"
        f"Today PnL:    <b>${stats['pnl']:.2f}</b> ({stats['count']} trades)\n"
    )

    await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=_main_keyboard()
    )


# ── /status ───────────────────────────────────────────────────────────────────

@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    trades = get_open_trades()
    if not trades:
        msg = "📭 No open trades."
    else:
        lines = ["📈 <b>Open Trades</b>\n"]
        for t in trades:
            disp = normalize_symbol(t.symbol)
            lines.append(
                f"• <b>{html.escape(disp)}</b> [{t.side.upper()}]\n"
                f"  Entry: {t.entry_price:.6f}\n"
                f"  SL: <b>{t.stop_loss:.6f}</b>  TP: <b>{t.take_profit:.6f}</b>\n"
                f"  Signal: {t.signal_type or '-'}\n"
            )
        msg = "\n".join(lines)

    await update.effective_message.reply_text(msg, parse_mode="HTML")


# ── /toggle ───────────────────────────────────────────────────────────────────

@admin_only
async def cmd_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_settings()
    new_state = not cfg.trading_enabled
    set_trading_enabled(new_state)
    label = "🟢 Enabled" if new_state else "🔴 Paused"
    await update.effective_message.reply_text(
        f"Trading is now <b>{label}</b>", parse_mode="HTML"
    )


# ── /setrisk ──────────────────────────────────────────────────────────────────

@admin_only
async def cmd_setrisk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_settings()
    if not ctx.args:
        await update.effective_message.reply_text(
            f"💰 Current risk: <b>{cfg.risk_percent}%</b>\n\n"
            f"Usage: <code>/setrisk 1.5</code>  (0.1 – 10)",
            parse_mode="HTML",
        )
        return
    try:
        val = float(ctx.args[0])
        if not 0.1 <= val <= 10:
            raise ValueError
        set_risk_percent(val)
        await update.effective_message.reply_text(
            f"✅ Risk set to <b>{val}%</b>", parse_mode="HTML"
        )
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid value. Use a number between 0.1 and 10.\nExample: <code>/setrisk 1.5</code>",
            parse_mode="HTML",
        )


# ── /setamount ────────────────────────────────────────────────────────────────

@admin_only
async def cmd_setamount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_settings()
    if not ctx.args:
        current = f"${cfg.trade_amount_usdt:.2f} USDT" if cfg.trade_amount_usdt else f"{cfg.risk_percent}% risk"
        await update.effective_message.reply_text(
            f"💵 <b>Trade Amount</b>\n"
            f"Current: <b>{current}</b>\n\n"
            f"  <code>/setamount 100</code>  → $100 per trade\n"
            f"  <code>/setamount 0</code>    → revert to risk-% sizing",
            parse_mode="HTML",
        )
        return
    try:
        val = float(ctx.args[0])
        if val < 0:
            raise ValueError
        if val == 0:
            set_trade_amount(None)
            cfg2 = get_settings()
            await update.effective_message.reply_text(
                f"✅ Reverted to risk-% sizing (<b>{cfg2.risk_percent}%</b>)", parse_mode="HTML"
            )
        else:
            set_trade_amount(val)
            await update.effective_message.reply_text(
                f"✅ Trade amount set to <b>${val:.2f} USDT</b> per signal", parse_mode="HTML"
            )
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid value. Example: <code>/setamount 100</code>", parse_mode="HTML"
        )


# ── /settimeframe ─────────────────────────────────────────────────────────────

VALID_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]


@admin_only
async def cmd_settimeframe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_settings()
    if not ctx.args:
        opts = "  ".join(f"<code>{tf}</code>" for tf in VALID_TIMEFRAMES)
        await update.effective_message.reply_text(
            f"⏱ <b>Timeframe</b>\n"
            f"Current: <b>{cfg.timeframe or '15m'}</b>\n\n"
            f"Available: {opts}\n\n"
            f"Usage: <code>/settimeframe 1h</code>",
            parse_mode="HTML",
        )
        return
    tf = ctx.args[0].lower()
    if tf not in VALID_TIMEFRAMES:
        await update.effective_message.reply_text(
            f"❌ Invalid timeframe. Choose from: {', '.join(VALID_TIMEFRAMES)}", parse_mode="HTML"
        )
        return
    set_timeframe(tf)
    await update.effective_message.reply_text(
        f"✅ Timeframe set to <b>{tf}</b>", parse_mode="HTML"
    )


# ── /pairs ────────────────────────────────────────────────────────────────────

@admin_only
async def cmd_pairs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    pairs = get_active_pairs()
    if not pairs:
        await update.effective_message.reply_text(
            "📋 <b>Active Pairs</b>\n\nNo pairs added yet.\n\n"
            "Add one:    <code>/addpair BTC/USDT</code>\n"
            "Load top:   <code>/loadtop 20</code>",
            parse_mode="HTML",
        )
        return
    await update.effective_message.reply_text(
        _pairs_message(pairs, 0),
        parse_mode="HTML",
        reply_markup=_pairs_keyboard(pairs, 0),
    )


# ── /addpair ──────────────────────────────────────────────────────────────────

@admin_only
async def cmd_addpair(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.effective_message.reply_text(
            "Usage: <code>/addpair BTC/USDT</code>", parse_mode="HTML"
        )
        return
    symbol = normalize_symbol(ctx.args[0])
    if "/" not in symbol:
        await update.effective_message.reply_text(
            "❌ Invalid format. Use <code>BASE/QUOTE</code> e.g. <code>BTC/USDT</code>",
            parse_mode="HTML",
        )
        return
    pairs = get_active_pairs()
    if len(pairs) >= 50:
        await update.effective_message.reply_text(
            "⚠️ Maximum of 50 pairs reached. Remove some before adding more.",
            parse_mode="HTML",
        )
        return
    if symbol not in pairs:
        pairs.append(symbol)
        set_active_pairs(pairs)
        await update.effective_message.reply_text(
            f"✅ Added <b>{html.escape(symbol)}</b>  ({len(pairs)}/50 pairs)",
            parse_mode="HTML",
        )
    else:
        await update.effective_message.reply_text(
            f"⚠️ <b>{html.escape(symbol)}</b> is already in the list.", parse_mode="HTML"
        )


# ── /removepair ───────────────────────────────────────────────────────────────

@admin_only
async def cmd_removepair(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.effective_message.reply_text(
            "Usage: <code>/removepair ETH/USDT</code>", parse_mode="HTML"
        )
        return
    symbol = normalize_symbol(ctx.args[0])
    pairs = get_active_pairs()
    if symbol in pairs:
        pairs.remove(symbol)
        set_active_pairs(pairs)
        remaining = f"{len(pairs)} pairs remaining"
        await update.effective_message.reply_text(
            f"✅ Removed <b>{html.escape(symbol)}</b>  ({remaining})",
            parse_mode="HTML",
        )
    else:
        await update.effective_message.reply_text(
            f"⚠️ <b>{html.escape(symbol)}</b> not found in active pairs.", parse_mode="HTML"
        )


# ── /loadtop ──────────────────────────────────────────────────────────────────

@admin_only
async def cmd_loadtop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /loadtop [N]   — replace active pairs with the top-N Bitget USDT pairs
                     by 24h volume.  N defaults to 20, max 50.
    """
    n = 20
    if ctx.args:
        try:
            n = int(ctx.args[0])
            if not 1 <= n <= 50:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text(
                "❌ Usage: <code>/loadtop 20</code>  (1 – 50)", parse_mode="HTML"
            )
            return

    msg = await update.effective_message.reply_text(
        f"🌐 Fetching top {n} USDT pairs from Bitget by 24h volume…"
    )
    try:
        from trading.executor import fetch_top_usdt_pairs
        pairs = fetch_top_usdt_pairs(n)
        if not pairs:
            await msg.edit_text("❌ No pairs returned — Bitget may be unreachable.")
            return
        set_active_pairs(pairs)
        pair_list = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(pairs))
        await msg.edit_text(
            f"✅ Loaded <b>{len(pairs)}</b> pairs:\n\n<code>{pair_list}</code>\n\n"
            f"Use 📋 My Pairs to manage them.",
            parse_mode="HTML",
        )
    except Exception as exc:
        await msg.edit_text(
            f"❌ Error fetching pairs: <code>{html.escape(str(exc))}</code>",
            parse_mode="HTML",
        )


# ── /balance ──────────────────────────────────────────────────────────────────

@admin_only
async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from trading.executor import fetch_usdt_balance
        balance = fetch_usdt_balance()
        await update.effective_message.reply_text(
            f"💰 Spot balance: <b>${balance:.2f} USDT</b>", parse_mode="HTML"
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Error fetching balance: {html.escape(str(exc))}", parse_mode="HTML"
        )


# ── /closeall ─────────────────────────────────────────────────────────────────

@admin_only
async def cmd_closeall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("🚨 Closing all positions…")
    try:
        from trading.executor import close_all_positions
        count = close_all_positions()
        await update.effective_message.reply_text(
            f"✅ Closed <b>{count}</b> position(s).", parse_mode="HTML"
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Error: {html.escape(str(exc))}", parse_mode="HTML"
        )


# ── Free-text handler (amount input after button prompt) ──────────────────────

@admin_only
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    state = _waiting_for.get(user_id)

    if state == "amount":
        raw = (update.effective_message.text or "").strip()
        try:
            val = float(raw)
            if val < 0:
                raise ValueError
            if val == 0:
                set_trade_amount(None)
                cfg = get_settings()
                await update.effective_message.reply_text(
                    f"✅ Reverted to risk-% sizing (<b>{cfg.risk_percent}%</b>)\n\nType /menu to go back.",
                    parse_mode="HTML",
                )
            else:
                set_trade_amount(val)
                await update.effective_message.reply_text(
                    f"✅ Trade amount set to <b>${val:.2f} USDT</b> per signal\n\nType /menu to go back.",
                    parse_mode="HTML",
                )
        except ValueError:
            await update.effective_message.reply_text(
                "❌ Please enter a valid number (e.g. <code>100</code> for $100 USDT).\n"
                "Send <code>0</code> to revert to risk-% sizing.",
                parse_mode="HTML",
            )
        finally:
            _waiting_for.pop(user_id, None)

    elif state == "bulk_pairs":
        raw = (update.effective_message.text or "").strip()
        import re
        # Split by comma, space, or newline
        symbols = re.split(r'[,\s\n]+', raw)
        valid_symbols = []
        for s in symbols:
            s = normalize_symbol(s)
            if "/" in s and s not in valid_symbols:
                valid_symbols.append(s)
        
        if not valid_symbols:
            await update.effective_message.reply_text(
                "❌ No valid symbols found. Please use <code>BASE/QUOTE</code> format.",
                parse_mode="HTML",
            )
            return

        # Limit to 50 total
        current_pairs = get_active_pairs()
        new_list = list(dict.fromkeys(current_pairs + valid_symbols))[:50]
        set_active_pairs(new_list)
        
        added_count = len(new_list) - len(current_pairs)
        await update.effective_message.reply_text(
            f"✅ Processed bulk request.\n"
            f"Added: <b>{added_count}</b> new pairs.\n"
            f"Total active: <b>{len(new_list)}/50</b>.\n\n"
            f"Type /menu to go back.",
            parse_mode="HTML",
        )
        _waiting_for.pop(user_id, None)


# ── Inline button router ──────────────────────────────────────────────────────

@admin_only
async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "noop":
        return

    elif data == "status":
        await cmd_status(update, ctx)

    elif data == "toggle":
        await cmd_toggle(update, ctx)

    elif data == "balance":
        await cmd_balance(update, ctx)

    elif data == "pairs":
        await cmd_pairs(update, ctx)

    elif data == "closeall":
        await cmd_closeall(update, ctx)

    elif data == "menu":
        await cmd_menu(update, ctx)

    # ── Pairs pagination ──────────────────────────────────────────────────────
    elif data.startswith("pairs_pg:"):
        page = int(data.split(":")[1])
        pairs = get_active_pairs()
        await q.message.edit_text(
            _pairs_message(pairs, page),
            parse_mode="HTML",
            reply_markup=_pairs_keyboard(pairs, page),
        )

    # ── Inline pair removal ───────────────────────────────────────────────────
    elif data.startswith("rm_pair:"):
        # Format: rm_pair:SYMBOL:PAGE
        parts = data.split(":", 2)
        symbol = parts[1] if len(parts) > 1 else ""
        page   = int(parts[2]) if len(parts) > 2 else 0
        pairs  = get_active_pairs()
        if symbol in pairs:
            pairs.remove(symbol)
            set_active_pairs(pairs)
            if pairs:
                # Clamp page in case we removed the last item on last page
                total_pages = max(1, math.ceil(len(pairs) / PAIRS_PAGE_SIZE))
                page = min(page, total_pages - 1)
                await q.message.edit_text(
                    f"✅ Removed <b>{html.escape(symbol)}</b>\n\n"
                    + _pairs_message(pairs, page),
                    parse_mode="HTML",
                    reply_markup=_pairs_keyboard(pairs, page),
                )
            else:
                await q.message.edit_text(
                    f"✅ Removed <b>{html.escape(symbol)}</b>\n\n"
                    f"📋 No pairs left. Add one: <code>/addpair BTC/USDT</code>\n"
                    f"Or load top: <code>/loadtop 20</code>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")
                    ]]),
                )
        else:
            await update.effective_message.reply_text(
                f"⚠️ {html.escape(symbol)} not found (already removed?)."
            )

    # ── Set Amount ────────────────────────────────────────────────────────────
    elif data == "set_amount":
        cfg = get_settings()
        current = (
            f"${cfg.trade_amount_usdt:.2f} USDT"
            if cfg.trade_amount_usdt
            else f"{cfg.risk_percent}% risk"
        )
        _waiting_for[update.effective_user.id] = "amount"
        await update.effective_message.reply_text(
            f"💵 <b>Set Trade Amount</b>\n"
            f"Current: <b>{current}</b>\n\n"
            f"Reply with the USDT amount per trade.\n"
            f"Example: <code>100</code> → $100 per trade\n"
            f"Send <code>0</code> to revert to risk-% sizing.",
            parse_mode="HTML",
        )

    # ── Timeframe menu ────────────────────────────────────────────────────────
    elif data == "set_tf_menu":
        cfg = get_settings()
        current_tf = cfg.timeframe or "15m"
        tf_buttons: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for tf in VALID_TIMEFRAMES:
            label = f"✅ {tf}" if tf == current_tf else tf
            row.append(InlineKeyboardButton(label, callback_data=f"set_tf:{tf}"))
            if len(row) == 3:
                tf_buttons.append(row)
                row = []
        if row:
            tf_buttons.append(row)
        tf_buttons.append([InlineKeyboardButton("🔙 Back", callback_data="menu")])
        await update.effective_message.reply_text(
            f"⏱ <b>Select Timeframe</b>\n"
            f"Current: <b>{current_tf}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(tf_buttons),
        )

    elif data.startswith("set_tf:"):
        tf = data.split(":", 1)[1]
        if tf in VALID_TIMEFRAMES:
            set_timeframe(tf)
            await update.effective_message.reply_text(
                f"✅ Timeframe set to <b>{tf}</b>\n\nType /menu to go back.",
                parse_mode="HTML",
            )
        else:
            await update.effective_message.reply_text("❌ Unknown timeframe.")

    # ── Load Top Pairs menu ───────────────────────────────────────────────────
    elif data == "load_top_menu":
        rows = []
        row = []
        for n in [10, 20, 30, 50]:
            row.append(InlineKeyboardButton(f"Top {n}", callback_data=f"load_top:{n}"))
        rows.append(row)
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu")])
        await update.effective_message.reply_text(
            "🌐 <b>Load Top USDT Pairs</b>\n\n"
            "Replaces your current pair list with the highest-volume\n"
            "USDT pairs on Bitget spot.\n\n"
            "How many pairs do you want to monitor?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif data.startswith("load_top:"):
        n = int(data.split(":")[1])
        await q.message.edit_text(
            f"🌐 Fetching top {n} USDT pairs from Bitget…"
        )
        try:
            from trading.executor import fetch_top_usdt_pairs
            pairs = fetch_top_usdt_pairs(n)
            if not pairs:
                await q.message.edit_text("❌ No pairs returned — Bitget may be unreachable.")
                return
            set_active_pairs(pairs)
            pair_list = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(pairs))
            await q.message.edit_text(
                f"✅ Loaded <b>{len(pairs)}</b> top pairs:\n\n"
                f"<code>{pair_list}</code>\n\n"
                f"Use 📋 My Pairs to view/manage them.",
                parse_mode="HTML",
            )
        except Exception as exc:
            await q.message.edit_text(
                f"❌ Error: <code>{html.escape(str(exc))}</code>",
                parse_mode="HTML",
            )

    # ── Add Bulk Pairs ────────────────────────────────────────────────────────
    elif data == "add_bulk":
        _waiting_for[update.effective_user.id] = "bulk_pairs"
        await update.effective_message.reply_text(
            "➕ <b>Add Bulk Pairs</b>\n\n"
            "Please send a list of symbols separated by commas or spaces.\n"
            "Maximum 50 pairs total.\n\n"
            "Example: <code>BTC/USDT, ETH/USDT, SOL/USDT</code>",
            parse_mode="HTML",
        )

    else:
        logger.warning("Unknown callback_data: %s", data)


# ── Alert formatters ──────────────────────────────────────────────────────────

def alert_signal(symbol: str, signal) -> None:
    disp = normalize_symbol(symbol)
    send_alert(
        f"📡 <b>SMC Signal — {html.escape(disp)}</b>\n\n"
        f"Side:        <b>{signal.side.upper()}</b>\n"
        f"Entry:       <b>{signal.entry:.6f}</b>\n"
        f"Stop Loss:   <b>{signal.stop_loss:.6f}</b>\n"
        f"Take Profit: <b>{signal.take_profit:.6f}</b>\n"
        f"Type:        <b>{signal.signal_type}</b>\n\n"
        f"<i>{html.escape(signal.reason)}</i>"
    )


def alert_trade_opened(symbol: str, trade) -> None:
    disp = normalize_symbol(symbol)
    send_alert(
        f"✅ <b>Trade Opened — {html.escape(disp)}</b>\n\n"
        f"Side:     <b>{trade.side.upper()}</b>\n"
        f"Entry:    <b>{trade.entry_price:.6f}</b>\n"
        f"Qty:      <b>{trade.quantity:.6f}</b>\n"
        f"SL:       <b>{trade.stop_loss:.6f}</b>\n"
        f"TP:       <b>{trade.take_profit:.6f}</b>\n"
        f"Order ID: <code>{trade.bitget_order_id or 'N/A'}</code>"
    )


def alert_trade_closed(trade, price: float, pnl: float, reason: str) -> None:
    disp = normalize_symbol(trade.symbol)
    emoji = "🟢" if pnl >= 0 else "🔴"
    send_alert(
        f"{emoji} <b>Trade Closed — {html.escape(disp)}</b>\n\n"
        f"Reason:    <b>{reason}</b>\n"
        f"Exit:      <b>{price:.6f}</b>\n"
        f"PnL:       <b>{'+' if pnl >= 0 else ''}{pnl:.4f} USDT</b>"
    )


def alert_error(context: str, exc: Exception) -> None:
    send_alert(
        f"⚠️ <b>Error in {html.escape(context)}</b>\n\n"
        f"<code>{html.escape(str(exc))[:300]}</code>"
    )


# ── App builder ───────────────────────────────────────────────────────────────

def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler(["start", "menu"], cmd_menu))
    app.add_handler(CommandHandler("status",        cmd_status))
    app.add_handler(CommandHandler("toggle",        cmd_toggle))
    app.add_handler(CommandHandler("balance",       cmd_balance))
    app.add_handler(CommandHandler("setrisk",       cmd_setrisk))
    app.add_handler(CommandHandler("setamount",     cmd_setamount))
    app.add_handler(CommandHandler("settimeframe",  cmd_settimeframe))
    app.add_handler(CommandHandler("pairs",         cmd_pairs))
    app.add_handler(CommandHandler("addpair",       cmd_addpair))
    app.add_handler(CommandHandler("removepair",    cmd_removepair))
    app.add_handler(CommandHandler("loadtop",       cmd_loadtop))
    app.add_handler(CommandHandler("closeall",      cmd_closeall))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app


def run_bot_in_thread(app: Application) -> None:
    global _bot_loop

    async def _run():
        global _bot_loop
        _bot_loop = asyncio.get_running_loop()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot polling started")
        while True:
            await asyncio.sleep(3600)

    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        except Exception as exc:
            logger.error("Telegram bot thread error: %s", exc)

    t = threading.Thread(target=_thread, name="telegram-bot", daemon=True)
    t.start()
    logger.info("Telegram bot thread started")
