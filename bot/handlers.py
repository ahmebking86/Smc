"""
All Telegram handlers — rebalance portfolio bot.
Supports Bitget + MEXC.
"""

from __future__ import annotations
import asyncio
import html as _html
import logging
import uuid as _uuid

from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler,
    CommandHandler, CallbackQueryHandler, MessageHandler, filters,
)

from config import TELEGRAM_CHAT_ID, MAX_ASSETS, MIN_ORDER_USDT
from bot.keyboards import (
    main_menu, confirm_cancel, back_main,
    close_all_confirm_kb, liquidate_wallet_confirm_kb,
    rebalance_mode_kb, portfolios_list, portfolio_actions,
    close_confirm, replace_asset_kb, delete_asset_kb, confirm_delete_kb,
    exchange_select_kb,
    exchange_choice_kb, confirm_add_asset_kb, asset_close_kb,
)
from bot.states import (
    WAIT_EXCHANGE_CHOICE,
    WAIT_API_KEY, WAIT_API_SECRET, WAIT_PASSPHRASE,
    WAIT_SYMBOLS, WAIT_TOTAL_AMOUNT, WAIT_ALLOCATIONS,
    WAIT_REBALANCE_MODE, WAIT_TIME_INTERVAL, WAIT_THRESHOLD_PCT, WAIT_CONFIRM,
    WAIT_REPLACE_NEW_SYMBOL, WAIT_REPLACE_CONFIRM,
    WAIT_ADD_FUNDS_AMOUNT,
    WAIT_REDUCE_FUNDS_AMOUNT,
    WAIT_ADD_ASSET_SYMBOL,
    WAIT_ADD_ASSET_AMOUNT,
    WAIT_ADD_ASSET_CONFIRM,
)
from trading.rebalance_engine import get_engine, PortfolioConfig, AssetConfig
from trading.bitget_client import get_bitget, invalidate_credentials_cache
from trading.mexc_client import get_mexc, invalidate_mexc_credentials_cache
from trading.monitor import pause_monitor, resume_monitor
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
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=markup, parse_mode=parse_mode
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text, reply_markup=markup, parse_mode=parse_mode
            )
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)

    bitget_ok = await asyncio.to_thread(get_bitget().has_credentials)
    mexc_ok   = await asyncio.to_thread(get_mexc().has_credentials)

    bitget_status = "🟢 متصلة" if bitget_ok else "🔴 غير متصلة"
    mexc_status   = "🟢 متصلة" if mexc_ok   else "🔴 غير متصلة"

    await _reply(update,
        "✨ <b>بوت إعادة توازن المحفظة</b> ✨\n"
        "<code>─────────────────────────</code>\n"
        "🤖 يدير محفظتك على <b>Bitget</b> و <b>MEXC</b>\n\n"
        "📊 <b>حالة الاتصال:</b>\n"
        f"• Bitget: {bitget_status}\n"
        f"• MEXC:   {mexc_status}\n\n"
        "👇 اختر من القائمة:",
        main_menu())


async def cb_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


# ── API setup (BitGet + MEXC) ─────────────────────────────────────────────────

async def cb_setup_api(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    await _reply(update,
        "🔑 <b>إعداد مفاتيح API</b>\n\n"
        "اختر المنصة:",
        exchange_choice_kb())
    return WAIT_EXCHANGE_CHOICE


async def cb_api_exchange(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if q.data == "api_exchange_bitget":
        ctx.user_data["api_exchange"] = "bitget"
        name = "BitGet"
    else:
        ctx.user_data["api_exchange"] = "mexc"
        name = "MEXC"
    await q.edit_message_text(
        f"🔑 <b>إعداد مفاتيح {name}</b>\n\n"
        "أرسل <b>API Key</b>:\n"
        "(يمكنك إلغاء العملية بـ /cancel)",
        parse_mode="HTML",
        reply_markup=back_main(),
    )
    return WAIT_API_KEY


async def cb_choose_exchange_api(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Legacy handler for exch_api_ pattern."""
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    exchange = "mexc" if "mexc" in q.data else "bitget"
    ctx.user_data.clear()
    ctx.user_data["api_exchange"] = exchange
    name = "MEXC" if exchange == "mexc" else "BitGet"
    await _reply(update,
        f"🔑 <b>إعداد مفاتيح {name}</b>\n\n"
        "أرسل <b>API Key</b>:\n"
        "(يمكنك إلغاء العملية بـ /cancel)",
        back_main())
    return WAIT_API_KEY


async def got_api_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["api_key"] = update.message.text.strip()
    await update.message.reply_text("أرسل <b>API Secret</b>:", parse_mode="HTML")
    return WAIT_API_SECRET


async def got_api_secret(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["api_secret"] = update.message.text.strip()
    exchange = ctx.user_data.get("api_exchange", "bitget")
    if exchange == "mexc":
        key = ctx.user_data.get("api_key", "")
        secret = ctx.user_data["api_secret"]
        await asyncio.to_thread(db.set_setting, "mexc_api_key", key)
        await asyncio.to_thread(db.set_setting, "mexc_api_secret", secret)
        invalidate_mexc_credentials_cache()
        client = get_mexc()
        ok, hint = await asyncio.to_thread(client.validate_credentials)
        if ok:
            await update.message.reply_text(
                "✅ <b>تم حفظ مفاتيح MEXC والتحقق بنجاح!</b>",
                parse_mode="HTML", reply_markup=main_menu())
        else:
            await update.message.reply_text(
                f"⚠️ حُفظت المفاتيح لكن التحقق فشل:\n<code>{_html.escape(hint)}</code>",
                parse_mode="HTML", reply_markup=main_menu())
        ctx.user_data.clear()
        return ConversationHandler.END
    await update.message.reply_text("أرسل <b>Passphrase</b>:", parse_mode="HTML")
    return WAIT_PASSPHRASE


async def got_passphrase(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    key = ctx.user_data.get("api_key", "")
    secret = ctx.user_data.get("api_secret", "")
    phrase = update.message.text.strip()
    await asyncio.to_thread(db.set_setting, "bitget_api_key", key)
    await asyncio.to_thread(db.set_setting, "bitget_api_secret", secret)
    await asyncio.to_thread(db.set_setting, "bitget_passphrase", phrase)
    invalidate_credentials_cache()
    client = get_bitget()
    ok, hint = await asyncio.to_thread(client.validate_credentials)
    if ok:
        await update.message.reply_text(
            "✅ <b>تم حفظ مفاتيح Bitget والتحقق بنجاح!</b>",
            parse_mode="HTML", reply_markup=main_menu())
    else:
        await update.message.reply_text(
            f"⚠️ حُفظت المفاتيح لكن التحقق فشل:\n<code>{_html.escape(hint)}</code>",
            parse_mode="HTML", reply_markup=main_menu())
    ctx.user_data.clear()
    return ConversationHandler.END


# ── Create portfolio conversation ─────────────────────────────────────────────

async def cb_new_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    ctx.user_data.clear()
    await _reply(update,
        "🆕 <b>إنشاء محفظة إعادة توازن</b>\n\n"
        "اختر المنصة:",
        exchange_select_kb("new"))
    return WAIT_EXCHANGE_CHOICE  # FIXED: was ConversationHandler.END


async def cb_choose_exchange_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    data = q.data  # exch_new_bitget | exch_new_mexc
    exchange = "mexc" if "mexc" in data else "bitget"
    ctx.user_data.clear()
    ctx.user_data["exchange"] = exchange

    client = get_mexc() if exchange == "mexc" else get_bitget()
    name = "MEXC" if exchange == "mexc" else "BitGet"
    if not await asyncio.to_thread(client.has_credentials):
        await _reply(update,
            f"⚠️ يجب إعداد مفاتيح {name} API أولاً.\nاضغط ⚙️ إعدادات API.",
            main_menu())
        return ConversationHandler.END

    await _reply(update,
        f"🆕 <b>إنشاء محفظة على {name}</b>\n\n"
        f"أدخل أسماء العملات مفصولة بفاصلة (حد أقصى {MAX_ASSETS}):\n"
        "مثال: <code>BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT</code>\n\n"
        "💡 يمكنك إدخال عملة واحدة فقط.")
    return WAIT_SYMBOLS


async def got_symbols(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().upper()
    parts = [s.strip() for s in raw.replace("،", ",").replace(" ", ",").split(",") if s.strip()]
    if not parts:
        await update.message.reply_text("❌ أدخل عملة واحدة على الأقل:")
        return WAIT_SYMBOLS

    seen, unique = set(), []
    for s in parts:
        if not s.endswith("USDT"):
            s = s + "USDT"
        if s not in seen:
            seen.add(s)
            unique.append(s)

    if len(unique) > MAX_ASSETS:
        await update.message.reply_text(f"❌ الحد الأقصى {MAX_ASSETS} عملة. أعدت {len(unique)}.")
        return WAIT_SYMBOLS

    exchange = ctx.user_data.get("exchange", "bitget")
    client = get_mexc() if exchange == "mexc" else get_bitget()

    async def _check(sym):
        try:
            p = await asyncio.to_thread(client.get_price, sym)
            return sym, p, None
        except Exception as e:
            return sym, None, e

    results = await asyncio.gather(*[_check(s) for s in unique])
    valid, invalid = [], []
    for sym, price, err in results:
        if err is None:
            valid.append((sym, price))
        else:
            invalid.append(sym)

    if not valid:
        await update.message.reply_text(f"❌ لم أجد أي عملة على {('MEXC' if exchange == 'mexc' else 'BitGet')}. حاول مجدداً:")
        return WAIT_SYMBOLS

    ctx.user_data["symbols"] = [s for s, _ in valid]
    ctx.user_data["symbol_prices"] = {s: p for s, p in valid}

    lines = ["✅ <b>العملات المعتمدة:</b>"]
    for sym, price in valid:
        lines.append(f"  • <code>{sym}</code> — {price:.6f}")
    if invalid:
        lines.append(f"\n⚠️ لم تُعثر: <code>{', '.join(invalid)}</code>")
    lines.append(
        f"\n<b>الخطوة 2</b> — أدخل <b>مبلغ الاستثمار الكلي</b> (USDT):\n"
        "مثال: <code>1000</code>"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    return WAIT_TOTAL_AMOUNT


async def got_total_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip().replace(",", ""))
        assert val >= MIN_ORDER_USDT * len(ctx.user_data.get("symbols", [1]))
    except Exception:
        await update.message.reply_text(
            f"❌ أدخل مبلغاً رقمياً أكبر من أو يساوي "
            f"{MIN_ORDER_USDT * len(ctx.user_data.get('symbols', [1])):.0f} USDT:")
        return WAIT_TOTAL_AMOUNT
    ctx.user_data["total_amount"] = val
    symbols = ctx.user_data["symbols"]
    n = len(symbols)
    equal = round(100.0 / n, 2)
    example_parts = [f"{s.replace('USDT','')}={equal}" for s in symbols[:3]]
    if n > 3:
        example_parts.append("...")
    example = ", ".join(example_parts)

    await update.message.reply_text(
        f"✅ المبلغ: <b>{val:.2f} USDT</b>\n\n"
        f"<b>الخطوة 3</b> — أدخل <b>نسب التوزيع</b> (المجموع = 100%):\n\n"
        f"• <code>متساوي</code> → توزيع متساوٍ على الكل\n"
        f"• <code>BTC=50</code> → BTC 50% والباقي يتقاسمون الـ 50% بالتساوي\n"
        f"• <code>BTC=40, ETH=20</code> → والباقي يتقاسمون الـ 40%\n"
        f"• <code>{example}</code> → تحديد الكل يدوياً\n\n"
        f"💡 حدّد عملة أو أكثر، والباقي يُحسب تلقائياً.",
        parse_mode="HTML",
    )
    return WAIT_ALLOCATIONS


async def got_allocations(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    symbols = ctx.user_data["symbols"]
    allocations: dict[str, float] = {}

    if text in ("متساوي", "equal", "EQUAL", "متساو"):
        equal = 100.0 / len(symbols)
        for s in symbols:
            allocations[s] = equal
    else:
        raw = text.replace("%", "").replace("،", ",").upper()
        pairs = []
        if "=" in raw:
            for part in raw.replace(" ", "").split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    pairs.append((k.strip(), v.strip()))
        else:
            tokens = raw.replace(",", " ").split()
            i = 0
            while i < len(tokens) - 1:
                pairs.append((tokens[i], tokens[i + 1]))
                i += 2

        for k, v in pairs:
            sym = k if k.endswith("USDT") else k + "USDT"
            try:
                pct = float(v)
            except ValueError:
                await update.message.reply_text(f"❌ نسبة غير صالحة: {v}")
                return WAIT_ALLOCATIONS
            if sym not in symbols:
                await update.message.reply_text(
                    f"❌ العملة <code>{sym}</code> ليست ضمن القائمة.\n"
                    f"العملات: {', '.join(symbols)}",
                    parse_mode="HTML")
                return WAIT_ALLOCATIONS
            allocations[sym] = pct

        missing = [s for s in symbols if s not in allocations]
        specified_total = sum(allocations.values())
        if missing:
            remainder = 100.0 - specified_total
            if remainder < -0.05:
                await update.message.reply_text(
                    f"❌ مجموع النسب المحددة = <b>{specified_total:.2f}%</b> أكبر من 100%.",
                    parse_mode="HTML")
                return WAIT_ALLOCATIONS
            if remainder < 0.05 and len(missing) > 0:
                await update.message.reply_text(
                    f"❌ استهلكت 100% ولم يتبقَّ شيء لـ: {', '.join(m.replace('USDT','') for m in missing)}")
                return WAIT_ALLOCATIONS
            each = remainder / len(missing)
            for s in missing:
                allocations[s] = each

    total_pct = sum(allocations.values())
    if abs(total_pct - 100.0) > 0.1:
        await update.message.reply_text(
            f"❌ مجموع النسب = <b>{total_pct:.2f}%</b> — يجب أن يكون 100%.\nحاول مجدداً:",
            parse_mode="HTML")
        return WAIT_ALLOCATIONS

    for s in symbols:
        if s not in allocations:
            await update.message.reply_text(f"❌ ناقصة نسبة لـ {s}")
            return WAIT_ALLOCATIONS

    ctx.user_data["allocations"] = allocations

    lines = ["✅ <b>التوزيع:</b>"]
    total_amt = ctx.user_data["total_amount"]
    for s in symbols:
        pct = allocations[s]
        usdt = total_amt * pct / 100
        lines.append(f"  • <code>{s}</code>: {pct:.2f}% ≈ {usdt:.2f} USDT")
    lines.append("\n<b>الخطوة 4</b> — كيف تريد إعادة التوازن؟")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML",
                                    reply_markup=rebalance_mode_kb())
    return WAIT_REBALANCE_MODE


async def cb_mode_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    ctx.user_data["rebalance_mode"] = "time"
    await _reply(update,
        "⏰ <b>إعادة توازن بالوقت</b>\n\n"
        "كل كم ساعة تريد إعادة التوازن؟\n"
        "مثال: <code>6</code>  ← كل 6 ساعات\n"
        "مثال: <code>24</code> ← مرة يومياً")
    return WAIT_TIME_INTERVAL


async def cb_mode_percent(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    ctx.user_data["rebalance_mode"] = "percent"
    await _reply(update,
        "📐 <b>إعادة توازن بالنسبة %</b>\n\n"
        "ما نسبة الانحراف التي تُفعّل إعادة التوازن؟\n"
        "مثال: <code>5</code>  ← عندما تنحرف أي عملة ±5% عن نسبتها المستهدفة\n"
        "مثال: <code>10</code>")
    return WAIT_THRESHOLD_PCT


async def got_time_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip())
        assert 0.25 <= val <= 720
    except Exception:
        await update.message.reply_text("❌ أدخل رقماً بين 0.25 و 720 ساعة:")
        return WAIT_TIME_INTERVAL
    ctx.user_data["interval_hours"] = val
    ctx.user_data["threshold_pct"] = 0.0
    return await _show_confirm(update, ctx)


async def got_threshold_pct(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip().replace("%", ""))
        assert 0.5 <= val <= 50
    except Exception:
        await update.message.reply_text("❌ أدخل نسبة بين 0.5 و 50:")
        return WAIT_THRESHOLD_PCT
    ctx.user_data["threshold_pct"] = val
    ctx.user_data["interval_hours"] = 0.0
    return await _show_confirm(update, ctx)


async def _show_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    symbols = ctx.user_data["symbols"]
    allocs = ctx.user_data["allocations"]
    total = ctx.user_data["total_amount"]
    mode = ctx.user_data["rebalance_mode"]
    exch = ctx.user_data.get("exchange", "bitget").upper()
    lines = [
        f"📋 <b>ملخص المحفظة — تأكيد الإنشاء</b> [{exch}]",
        "<code>─────────────────────────</code>",
        f"💰 المبلغ الكلي: <b>{total:.2f} USDT</b>",
        f"🪙 عدد العملات: <b>{len(symbols)}</b>",
        "",
    ]
    for s in symbols:
        pct = allocs[s]
        lines.append(f"  • <code>{s}</code>: {pct:.1f}% ≈ {total*pct/100:.2f} USDT")
    lines.append("")
    if mode == "time":
        lines.append(f"⏰ إعادة التوازن: كل <b>{ctx.user_data['interval_hours']}</b> ساعة")
    else:
        lines.append(f"📐 إعادة التوازن عند انحراف <b>±{ctx.user_data['threshold_pct']}%</b>")
    lines.append("\n⚠️ سيتم شراء العملات فوراً بسعر السوق.")
    lines.append("اضغط ✅ تأكيد للمتابعة.")

    target = update.message if update.message else update.callback_query.message
    await target.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=confirm_cancel())
    return WAIT_CONFIRM


async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "⏳ <b>جارٍ إنشاء المحفظة وشراء العملات...</b>", parse_mode="HTML")

    symbols = ctx.user_data["symbols"]
    allocs = ctx.user_data["allocations"]
    assets = [AssetConfig(symbol=s, target_pct=allocs[s]) for s in symbols]
    cfg = PortfolioConfig(
        total_investment=ctx.user_data["total_amount"],
        assets=assets,
        rebalance_mode=ctx.user_data["rebalance_mode"],
        interval_hours=ctx.user_data.get("interval_hours", 0),
        threshold_pct=ctx.user_data.get("threshold_pct", 0),
        exchange=ctx.user_data.get("exchange", "bitget"),
    )
    engine = get_engine(cfg.exchange)
    try:
        portfolio = await asyncio.to_thread(engine.create_portfolio, cfg)
    except Exception as e:
        await update.callback_query.message.reply_text(
            f"❌ فشل الإنشاء:\n{_html.escape(str(e))}",
            parse_mode="HTML", reply_markup=main_menu())
        ctx.user_data.clear()
        return ConversationHandler.END

    lines = [
        f"✅ <b>تم إنشاء المحفظة</b> <code>{portfolio.id[:8]}</code>\n",
    ]
    for r in getattr(portfolio, "_create_results", []):
        lines.append(r)
    for e in getattr(portfolio, "_create_errors", []):
        lines.append(e)
    lines.append("\nالمحفظة نشطة وسيتم إعادة توازنها تلقائياً.")
    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=main_menu())
    ctx.user_data.clear()
    return ConversationHandler.END


async def cb_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
    await _reply(update, "❌ تم الإلغاء.", main_menu())
    return ConversationHandler.END


# ── Active portfolios ─────────────────────────────────────────────────────────

async def cb_active_portfolios(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    engine = get_engine()
    db_rows = await asyncio.to_thread(db.list_active_portfolios)
    if not db_rows:
        await _reply(update, "📭 لا توجد محافظ نشطة.", back_main())
        return

    display = []
    for row in db_rows:
        p = engine.get_portfolio(row["id"])
        assets = p.assets if p else await asyncio.to_thread(db.get_portfolio_assets, row["id"])
        count = len([a for a in assets if getattr(a, "status", a.get("status") if isinstance(a, dict) else "active") == "active"]) if assets else 0
        display.append({
            "id": row["id"],
            "status": row.get("status", "active"),
            "asset_count": count,
            "total_investment": float(row.get("total_investment") or 0),
            "exchange": row.get("exchange", "bitget"),
        })

    lines = [f"📊 <b>المحافظ النشطة ({len(display)})</b>\n"]
    for d in display:
        lines.append(
            f"• <code>{d['id'][:8]}</code> [{d.get('exchange','bitget').upper()}] — "
            f"{d['asset_count']} عملة — {d['total_investment']:.0f} USDT"
        )
    lines.append("\nاختر محفظة للتحكم بها:")
    await _reply(update, "\n".join(lines), portfolios_list(display))


async def cb_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    pid = update.callback_query.data.replace("portfolio_", "")
    engine = get_engine()
    p = engine.get_portfolio(pid)
    if not p:
        row = await asyncio.to_thread(db.get_portfolio, pid)
        if not row or row.get("status") == "closed":
            await _reply(update, "❌ المحفظة غير موجودة أو مغلقة.", back_main())
            return
        await _reply(update, "⚠️ المحفظة في قاعدة البيانات لكن غير محمّلة في الذاكرة. أعد تشغيل البوت.", back_main())
        return

    snap = await asyncio.to_thread(engine.snapshot, p)
    lines = [
        f"📊 <b>محفظة</b> <code>{pid[:8]}</code>",
        "<code>─────────────────────────</code>",
        f"💵 القيمة الحالية: <b>{snap['total_value']:.2f} USDT</b>",
        f"💰 الاستثمار الأولي: <b>{p.config.total_investment:.2f} USDT</b>",
    ]
    if p.config.rebalance_mode == "time":
        lines.append(f"⏰ الوضع: كل {p.config.interval_hours} ساعة")
    else:
        lines.append(f"📐 الوضع: انحراف ±{p.config.threshold_pct}%")
    if p.last_rebalance_at:
        lines.append(f"🕐 آخر توازن: {p.last_rebalance_at.strftime('%Y-%m-%d %H:%M')} UTC")
    lines.append("<code>─────────────────────────</code>")
    lines.append("<code>عملة   | هدف% | حالي% | انحراف</code>")
    for a in snap["assets"]:
        coin = a["coin"].ljust(6)
        lines.append(
            f"<code>{coin} | {a['target_pct']:5.1f} | {a['current_pct']:5.1f} | "
            f"{a['deviation']:+5.1f}</code>"
        )
    await _reply(update, "\n".join(lines), portfolio_actions(pid))


async def cb_rebalance_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    pid = update.callback_query.data.replace("rebalance_now_", "")
    engine = get_engine()
    p = engine.get_portfolio(pid)
    if not p:
        await _reply(update, "❌ المحفظة غير موجودة.", back_main())
        return
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⏳ جارٍ إعادة التوازن...")
    result = await asyncio.to_thread(engine.rebalance, p)
    lines = [f"🔄 <b>نتيجة إعادة التوازن</b>\n"]
    if result["actions"]:
        for a in result["actions"]:
            lines.append(f"  • {a}")
    else:
        lines.append("لا توجد عمليات — المحفظة متوازنة.")
    if result["errors"]:
        lines.append("\n⚠️ أخطاء:")
        for e in result["errors"]:
            lines.append(f"  • {_html.escape(str(e)[:120])}")
    lines.append(f"\n💵 القيمة: <b>{result['total_value']:.2f} USDT</b>")
    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=portfolio_actions(pid))


async def cb_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    pid = update.callback_query.data.replace("close_", "")
    await _reply(update,
        f"🛑 <b>إغلاق المحفظة</b> <code>{pid[:8]}</code>؟\n\n"
        "سيتم بيع جميع العملات بسعر السوق وإغلاق المحفظة.",
        close_confirm(pid))


async def cb_closeok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    pid = update.callback_query.data.replace("closeok_", "")
    engine = get_engine()
    p = engine.get_portfolio(pid)
    if not p:
        await _reply(update, "❌ المحفظة غير موجودة.", back_main())
        return
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⏳ جارٍ الإغلاق وبيع العملات...")
    pause_monitor()
    try:
        pnl = await asyncio.to_thread(engine.close_portfolio, p, True)
    finally:
        resume_monitor()
    await update.callback_query.message.reply_text(
        f"✅ تم إغلاق المحفظة <code>{pid[:8]}</code>\n"
        f"P&L تقريبي: <b>{pnl:+.4f} USDT</b>",
        parse_mode="HTML", reply_markup=main_menu())


async def cb_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    pid = update.callback_query.data.replace("pause_", "")
    engine = get_engine()
    p = engine.get_portfolio(pid)
    if not p:
        await _reply(update, "❌ غير موجودة.", back_main())
        return
    p.status = "paused"
    await asyncio.to_thread(db.update_portfolio, pid, {"status": "paused"})
    await _reply(update, f"⏸️ تم إيقاف المحفظة <code>{pid[:8]}</code> مؤقتاً.",
                 portfolio_actions(pid))


# ── Close all / Liquidate ─────────────────────────────────────────────────────

async def cb_close_all_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    await _reply(update,
        "🔴 <b>إيقاف وإغلاق جميع المحافظ؟</b>\n\n"
        "سيتم بيع جميع العملات وإغلاق كل المحافظ.\n"
        "⚠️ لا يمكن التراجع!",
        close_all_confirm_kb())


async def cb_close_all_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⏳ جارٍ إغلاق جميع المحافظ...")
    pause_monitor()
    engine = get_engine()
    closed = 0
    total_pnl = 0.0
    try:
        for p in list(engine.all_portfolios()):
            try:
                pnl = await asyncio.to_thread(engine.close_portfolio, p, False)
                total_pnl += pnl
                closed += 1
            except Exception as e:
                logger.error("Close %s: %s", p.id[:8], e)
    finally:
        resume_monitor()

    lines = [f"✅ أُغلقت {closed} محفظة"]
    lines.append(f"\n💵 P&L تقريبي: <b>{total_pnl:+.4f} USDT</b>")
    await update.callback_query.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=main_menu())


async def cb_liquidate_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    await _reply(update,
        "🧹 <b>تصفية المحفظة بالكامل؟</b>\n"
        "إلغاء كل الأوامر وبيع كل العملات → USDT.",
        liquidate_wallet_confirm_kb())


async def cb_liquidate_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("⏳ جارٍ التصفية...")
    # try both exchanges
    results = []
    for name, getter in [("Bitget", get_bitget), ("MEXC", get_mexc)]:
        try:
            client = getter()
            if await asyncio.to_thread(client.has_credentials):
                res = await asyncio.to_thread(client.liquidate_wallet)
                results.append(f"[{name}] أوامر: {res.get('cancelled_orders',0)}")
                for s in res.get("sold", [])[:5]:
                    results.append(f"  • {_html.escape(str(s))}")
        except Exception as e:
            results.append(f"[{name}] ❌ {e}")
    lines = ["✅ اكتملت التصفية"] + results
    await _reply(update, "\n".join(lines), back_main())


# ── Balance / PnL ─────────────────────────────────────────────────────────────

async def cb_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    lines = ["🏦 <b>رصيد المحفظة</b>\n"]
    for name, getter in [("Bitget", get_bitget), ("MEXC", get_mexc)]:
        try:
            client = getter()
            if not await asyncio.to_thread(client.has_credentials):
                lines.append(f"• {name}: غير متصل")
                continue
            balances = await asyncio.to_thread(client.get_account_balance)
            total_usdt = 0.0
            lines.append(f"\n<b>{name}:</b>")
            for item in balances:
                coin = (item.get("coin") or item.get("currency") or item.get("asset") or "").upper()
                avail = float(item.get("available") or item.get("availableBalance") or item.get("free") or 0)
                if avail <= 0:
                    continue
                if coin == "USDT":
                    val = avail
                else:
                    try:
                        price = await asyncio.to_thread(client.get_price, coin + "USDT")
                        val = avail * price
                    except Exception:
                        val = 0
                if val < 0.5 and coin != "USDT":
                    continue
                total_usdt += val
                lines.append(f"  • <code>{coin}</code>: {avail:.6g} ≈ {val:.2f} USDT")
            lines.append(f"  💵 الإجمالي: <b>{total_usdt:.2f} USDT</b>")
        except Exception as e:
            lines.append(f"• {name}: ❌ {_html.escape(str(e)[:80])}")
    await _reply(update, "\n".join(lines), back_main())


async def cb_total_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    rows = await asyncio.to_thread(db.list_active_portfolios)
    total = 0.0
    lines = ["💰 <b>ملخص المحافظ</b>\n"]
    for row in rows:
        trade_pnl = await asyncio.to_thread(db.portfolio_total_pnl, row["id"])
        total += trade_pnl
        lines.append(f"  • <code>{row['id'][:8]}</code>: {trade_pnl:+.2f} USDT")
    lines.append(f"\nالإجمالي التقريبي: <b>{total:+.2f} USDT</b>")
    lines.append("\n<i>ملاحظة: P&L تقريبي من صفقات الشراء/البيع المسجّلة.</i>")
    await _reply(update, "\n".join(lines), back_main())


# ── استبدال عملة ─────────────────────────────────────────────────────────────

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
    data = update.callback_query.data
    rest = data.replace("replace_pick_", "", 1)
    if len(rest) < 37:
        await _reply(update, "❌ بيانات غير صحيحة.", back_main())
        return ConversationHandler.END
    pid = rest[:36]
    old_symbol = rest[37:]
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


# ── حذف عملة ─────────────────────────────────────────────────────────────────

async def cb_delete_asset_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    portfolio_id = q.data.replace("delete_asset_", "")
    engine = get_engine()
    portfolio = engine._portfolios.get(portfolio_id)
    if not portfolio:
        await q.answer("المحفظة غير موجودة", show_alert=True)
        return
    assets = [a for a in portfolio.assets if a.status == "active"]
    await q.edit_message_text("اختر العملة المراد حذفها:", reply_markup=delete_asset_kb(portfolio_id, assets))


async def cb_delete_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.replace("delete_pick_", "").split("_", 1)
    portfolio_id, symbol = parts[0], parts[1]
    await q.edit_message_text(
        f"هل أنت متأكد من حذف وبيع <b>{symbol.replace('USDT','')}</b>؟",
        parse_mode="HTML",
        reply_markup=confirm_delete_kb(portfolio_id, symbol)
    )


async def cb_delete_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.replace("delete_ok_", "").split("_", 1)
    portfolio_id, symbol = parts[0], parts[1]
    engine = get_engine()
    portfolio = engine._portfolios.get(portfolio_id)
    if not portfolio:
        await q.answer("المحفظة غير موجودة", show_alert=True)
        return
    result = await asyncio.to_thread(engine.remove_asset, portfolio, symbol, True)
    msg = "\n".join(result.get("actions", []) + result.get("errors", []))
    await q.edit_message_text(msg or "تم", reply_markup=portfolio_actions(portfolio_id))


# ── زيادة الاستثمار ───────────────────────────────────────────────────────────

async def cb_add_funds_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    portfolio_id = q.data.replace("add_funds_", "")
    ctx.user_data["add_funds_pid"] = portfolio_id
    await q.edit_message_text("أرسل المبلغ بالـ USDT الذي تريد إضافته:")
    return WAIT_ADD_FUNDS_AMOUNT


async def got_add_funds_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", ""))
        if amount <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("❌ أدخل رقم صحيح أكبر من صفر:")
        return WAIT_ADD_FUNDS_AMOUNT
    pid = ctx.user_data.get("add_funds_pid")
    engine = get_engine()
    portfolio = engine.get_portfolio(pid) if hasattr(engine, "get_portfolio") else engine._portfolios.get(pid)
    if not portfolio:
        await update.message.reply_text("❌ المحفظة غير موجودة.", reply_markup=back_main())
        return ConversationHandler.END
    try:
        result = await asyncio.to_thread(engine.add_funds, portfolio, amount)
    except Exception as e:
        await update.message.reply_text(f"❌ فشل: {_html.escape(str(e))}", parse_mode="HTML", reply_markup=portfolio_actions(pid))
        return ConversationHandler.END
    msg = "\n".join(result.get("actions", []) + result.get("errors", []))
    await update.message.reply_text(msg or f"✅ تم إضافة {amount} USDT", reply_markup=portfolio_actions(pid))
    return ConversationHandler.END


# ── تخفيف الاستثمار ───────────────────────────────────────────────────────────

async def cb_reduce_funds_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    portfolio_id = q.data.replace("reduce_funds_", "")
    ctx.user_data["reduce_funds_pid"] = portfolio_id
    await q.edit_message_text(
        "➖ <b>تخفيف الاستثمار</b>\n\n"
        "أرسل المبلغ بالـ USDT اللي عايز تسحبه من المحفظة:\n"
        "مثال: <code>50</code>",
        parse_mode="HTML"
    )
    return WAIT_REDUCE_FUNDS_AMOUNT


async def got_reduce_funds_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", ""))
        if amount <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("❌ أدخل رقم صحيح أكبر من صفر:")
        return WAIT_REDUCE_FUNDS_AMOUNT
    pid = ctx.user_data.get("reduce_funds_pid")
    engine = get_engine()
    portfolio = engine.get_portfolio(pid) if hasattr(engine, "get_portfolio") else engine._portfolios.get(pid)
    if not portfolio:
        await update.message.reply_text("❌ المحفظة غير موجودة.", reply_markup=back_main())
        return ConversationHandler.END
    try:
        result = await asyncio.to_thread(engine.reduce_funds, portfolio, amount)
    except Exception as e:
        await update.message.reply_text(f"❌ فشل: {_html.escape(str(e))}", parse_mode="HTML", reply_markup=portfolio_actions(pid))
        return ConversationHandler.END
    msg = "\n".join(result.get("actions", []) + result.get("errors", []))
    await update.message.reply_text(msg or f"✅ تم سحب {amount} USDT", reply_markup=portfolio_actions(pid))
    return ConversationHandler.END


# ── تقرير الأداء ──────────────────────────────────────────────────────────────

async def cb_performance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    portfolio_id = q.data.replace("performance_", "")
    engine = get_engine()
    portfolio = engine._portfolios.get(portfolio_id)
    if not portfolio:
        await q.answer("المحفظة غير موجودة", show_alert=True)
        return
    report = engine.performance_report(portfolio)
    await q.edit_message_text(report, parse_mode="HTML", reply_markup=portfolio_actions(portfolio_id))


# ── بيع عملة واحدة من المحفظة ────────────────────────────────────────────────

async def cb_close_asset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not _authorized(update):
        return await _deny(update)
    # format: close_asset_{portfolio_id}_{symbol}
    parts = q.data.split("_", 3)
    if len(parts) < 4:
        await q.answer("بيانات غير صحيحة", show_alert=True)
        return
    portfolio_id = parts[2]
    symbol = parts[3]
    engine = get_engine()
    portfolio = engine._portfolios.get(portfolio_id)
    if not portfolio:
        await q.answer("المحفظة غير موجودة", show_alert=True)
        return
    try:
        result = await asyncio.to_thread(engine.remove_asset, portfolio, symbol, sell=True)
        actions = "\n".join(result.get("actions", []) + result.get("errors", []))
        await q.edit_message_text(
            f"✅ <b>تم بيع {symbol}</b>\n\n{actions}" if result.get("ok") else f"❌ فشل بيع {symbol}\n{actions}",
            parse_mode="HTML",
            reply_markup=portfolio_actions(portfolio_id),
        )
    except Exception as e:
        await q.edit_message_text(f"❌ خطأ: {_html.escape(str(e))}", parse_mode="HTML",
                                   reply_markup=portfolio_actions(portfolio_id))


# ── Cancel command ────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("تم الإلغاء.", reply_markup=main_menu())
    return ConversationHandler.END


# ── Build application ─────────────────────────────────────────────────────────



# ═══════════════════════════════════════════════════════════════
#  ➕ إضافة عملة جديدة (ميزة جديدة)
# ═══════════════════════════════════════════════════════════════

async def cb_add_asset_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """بداية عملية إضافة عملة جديدة للمحفظة"""
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END

    q = update.callback_query
    await q.answer()
    portfolio_id = q.data.replace("add_asset_", "")
    engine = get_engine()
    portfolio = engine.get_portfolio(portfolio_id) if hasattr(engine, "get_portfolio") else engine._portfolios.get(portfolio_id)

    if not portfolio:
        await _reply(update, "❌ المحفظة غير موجودة.", back_main())
        return ConversationHandler.END

    ctx.user_data.clear()
    ctx.user_data["add_asset_pid"] = portfolio_id

    await _reply(
        update,
        f"➕ <b>إضافة عملة جديدة</b>\n"
        f"محفظة: <code>{portfolio_id[:8]}</code>\n\n"
        "أرسل رمز العملة اللي عايز تضيفها:\n"
        "مثال: <code>SXT</code> أو <code>SXTUSDT</code>",
        back_main()
    )
    return WAIT_ADD_ASSET_SYMBOL


async def got_add_asset_symbol(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """استلام رمز العملة الجديدة"""
    raw = update.message.text.strip().upper()
    if not raw.endswith("USDT"):
        raw += "USDT"

    pid = ctx.user_data.get("add_asset_pid")
    engine = get_engine()
    portfolio = engine.get_portfolio(pid) if hasattr(engine, "get_portfolio") else engine._portfolios.get(pid)

    if not portfolio:
        await update.message.reply_text("❌ المحفظة غير موجودة.", reply_markup=back_main())
        return ConversationHandler.END

    # تحقق إن العملة مش موجودة أصلاً
    existing = [a.symbol for a in portfolio.assets if getattr(a, "status", "active") == "active"]
    if raw in existing:
        await update.message.reply_text(
            f"⚠️ العملة <b>{raw.replace('USDT','')}</b> موجودة بالفعل في المحفظة.\n"
            "جرب عملة تانية:",
            parse_mode="HTML"
        )
        return WAIT_ADD_ASSET_SYMBOL

    # تحقق إن العملة موجودة على المنصة
    try:
        exchange = getattr(portfolio, "exchange", None) or getattr(getattr(portfolio, "config", None), "exchange", "bitget")
        client = get_mexc() if exchange == "mexc" else get_bitget()
        price = await asyncio.to_thread(client.get_price, raw)
        if not price or price <= 0:
            raise ValueError("سعر غير صالح")
    except Exception as e:
        await update.message.reply_text(
            f"❌ مش لاقي العملة <b>{raw}</b> على المنصة.\n"
            f"تأكد من الاسم وحاول تاني.\n"
            f"<code>{_html.escape(str(e)[:80])}</code>",
            parse_mode="HTML"
        )
        return WAIT_ADD_ASSET_SYMBOL

    ctx.user_data["add_asset_symbol"] = raw
    ctx.user_data["add_asset_price"] = price

    await update.message.reply_text(
        f"✅ تم العثور على <b>{raw.replace('USDT','')}</b>\n"
        f"السعر الحالي: <b>{price:.6f}</b>\n\n"
        f"دلوقتي ابعت <b>مبلغ الاستثمار</b> بالـ USDT اللي عايز تخصصه للعملة دي:\n"
        f"مثال: <code>50</code>",
        parse_mode="HTML"
    )
    return WAIT_ADD_ASSET_AMOUNT


async def got_add_asset_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """استلام مبلغ الاستثمار للعملة الجديدة"""
    try:
        amount = float(update.message.text.strip().replace(",", ""))
        if amount < 5:
            raise ValueError("المبلغ صغير جداً")
    except Exception:
        await update.message.reply_text("❌ أدخل مبلغ رقمي صحيح (على الأقل 5 USDT):")
        return WAIT_ADD_ASSET_AMOUNT

    ctx.user_data["add_asset_amount"] = amount
    symbol = ctx.user_data.get("add_asset_symbol", "?")
    price = ctx.user_data.get("add_asset_price", 0)
    qty = amount / price if price > 0 else 0

    await update.message.reply_text(
        f"➕ <b>تأكيد إضافة العملة</b>\n\n"
        f"• العملة: <b>{symbol.replace('USDT','')}</b>\n"
        f"• المبلغ: <b>{amount:.2f} USDT</b>\n"
        f"• الكمية التقريبية: <b>{qty:.6f}</b>\n\n"
        f"هل تريد المتابعة؟",
        parse_mode="HTML",
        reply_markup=confirm_cancel()
    )
    return WAIT_ADD_ASSET_CONFIRM


async def cb_add_asset_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """تأكيد إضافة العملة"""
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END

    q = update.callback_query
    await q.answer()

    if q.data == "cancel":
        await _reply(update, "❌ تم الإلغاء.", back_main())
        ctx.user_data.clear()
        return ConversationHandler.END

    pid = ctx.user_data.get("add_asset_pid")
    symbol = ctx.user_data.get("add_asset_symbol")
    amount = ctx.user_data.get("add_asset_amount")

    if not all([pid, symbol, amount]):
        await _reply(update, "❌ بيانات ناقصة.", back_main())
        return ConversationHandler.END

    engine = get_engine()
    portfolio = engine.get_portfolio(pid) if hasattr(engine, "get_portfolio") else engine._portfolios.get(pid)

    if not portfolio:
        await _reply(update, "❌ المحفظة غير موجودة.", back_main())
        return ConversationHandler.END

    await q.edit_message_text("⏳ جارٍ إضافة العملة وشرائها...")

    try:
        if hasattr(engine, "add_asset"):
            result = await asyncio.to_thread(engine.add_asset, portfolio, symbol, amount)
        else:
            result = {"actions": [f"تمت إضافة {symbol} بمبلغ {amount} USDT"], "errors": ["دالة add_asset غير موجودة في الـ engine بعد"]}
    except Exception as e:
        await q.message.reply_text(
            f"❌ فشل إضافة العملة:\n<code>{_html.escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=portfolio_actions(pid)
        )
        ctx.user_data.clear()
        return ConversationHandler.END

    lines = [f"✅ <b>تم إضافة العملة بنجاح</b>\n"]
    for a in result.get("actions", []):
        lines.append(f"  • {a}")
    if result.get("errors"):
        lines.append("\n⚠️ ملاحظات:")
        for e in result["errors"]:
            lines.append(f"  • {_html.escape(str(e)[:100])}")

    await q.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=portfolio_actions(pid)
    )
    ctx.user_data.clear()
    return ConversationHandler.END


def build_application(app) -> None:
    api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_setup_api, pattern="^setup_api$")],
        states={
            WAIT_EXCHANGE_CHOICE: [
                CallbackQueryHandler(cb_api_exchange, pattern=r"^api_exchange_(bitget|mexc)$"),
            ],
            WAIT_API_KEY:      [MessageHandler(filters.TEXT & ~filters.COMMAND, got_api_key)],
            WAIT_API_SECRET:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_api_secret)],
            WAIT_PASSPHRASE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_passphrase)],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel, pattern="^cancel$"),
            CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
        ],
        allow_reentry=True,
    )

    new_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_new_portfolio, pattern="^new_portfolio$")],
        states={
            # FIXED: exchange choice is inside the conversation
            WAIT_EXCHANGE_CHOICE: [
                CallbackQueryHandler(cb_choose_exchange_new, pattern=r"^exch_new_"),
            ],
            WAIT_SYMBOLS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_symbols),
            ],
            WAIT_TOTAL_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_total_amount),
            ],
            WAIT_ALLOCATIONS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_allocations),
            ],
            WAIT_REBALANCE_MODE: [
                CallbackQueryHandler(cb_mode_time, pattern="^mode_time$"),
                CallbackQueryHandler(cb_mode_percent, pattern="^mode_percent$"),
            ],
            WAIT_TIME_INTERVAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_time_interval),
            ],
            WAIT_THRESHOLD_PCT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_threshold_pct),
            ],
            WAIT_CONFIRM: [
                CallbackQueryHandler(cb_confirm, pattern="^confirm$"),
                CallbackQueryHandler(cb_cancel, pattern="^cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CallbackQueryHandler(cb_cancel, pattern="^cancel$"),
            CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"),
        ],
        allow_reentry=True,
    )

    replace_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_replace_pick, pattern=r"^replace_pick_"),
        ],
        states={
            WAIT_REPLACE_NEW_SYMBOL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_replace_new_symbol)
            ],
            WAIT_REPLACE_CONFIRM: [
                CallbackQueryHandler(cb_replace_confirm, pattern="^(confirm|cancel)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    add_funds_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_add_funds_start, pattern=r"^add_funds_")],
        states={
            WAIT_ADD_FUNDS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_add_funds_amount)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    reduce_funds_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_reduce_funds_start, pattern=r"^reduce_funds_")],
        states={
            WAIT_REDUCE_FUNDS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_reduce_funds_amount)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(api_conv)
    app.add_handler(new_conv)
    app.add_handler(replace_conv)
    app.add_handler(add_funds_conv)
    app.add_handler(reduce_funds_conv)

    # ── إضافة عملة جديدة ──────────────────────────────────────
    add_asset_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_add_asset_start, pattern=r"^add_asset_")],
        states={
            WAIT_ADD_ASSET_SYMBOL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_add_asset_symbol)
            ],
            WAIT_ADD_ASSET_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_add_asset_amount)
            ],
            WAIT_ADD_ASSET_CONFIRM: [
                CallbackQueryHandler(cb_add_asset_confirm, pattern="^(confirm|cancel)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )
    app.add_handler(add_asset_conv)


    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(cb_active_portfolios, pattern="^active_portfolios$"))
    app.add_handler(CallbackQueryHandler(cb_portfolio, pattern="^portfolio_"))
    app.add_handler(CallbackQueryHandler(cb_rebalance_now, pattern="^rebalance_now_"))
    app.add_handler(CallbackQueryHandler(cb_close, pattern="^close_[a-f0-9-]{36}$"))
    app.add_handler(CallbackQueryHandler(cb_closeok, pattern="^closeok_"))
    app.add_handler(CallbackQueryHandler(cb_pause, pattern="^pause_"))
    app.add_handler(CallbackQueryHandler(cb_close_all_confirm, pattern="^close_all_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_close_all_ok, pattern="^close_all_ok$"))
    app.add_handler(CallbackQueryHandler(cb_liquidate_confirm, pattern="^liquidate_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_liquidate_ok, pattern="^liquidate_ok$"))
    app.add_handler(CallbackQueryHandler(cb_balance, pattern="^balance$"))
    app.add_handler(CallbackQueryHandler(cb_total_pnl, pattern="^total_pnl$"))
    app.add_handler(CallbackQueryHandler(cb_replace_start, pattern=r"^replace_[a-f0-9-]{36}$"))
    app.add_handler(CallbackQueryHandler(cb_delete_asset_start, pattern=r"^delete_asset_"))
    app.add_handler(CallbackQueryHandler(cb_delete_pick, pattern=r"^delete_pick_"))
    app.add_handler(CallbackQueryHandler(cb_delete_ok, pattern=r"^delete_ok_"))
    app.add_handler(CallbackQueryHandler(cb_performance, pattern=r"^performance_"))
    app.add_handler(CallbackQueryHandler(cb_choose_exchange_api, pattern=r"^exch_api_"))
    app.add_handler(CallbackQueryHandler(cb_close_asset, pattern=r"^close_asset_"))
    # NOTE: cb_choose_exchange_new is ONLY inside new_conv — do NOT register here
