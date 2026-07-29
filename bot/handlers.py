"""
All Telegram handlers — rebalance portfolio bot.
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
    rebalance_mode_kb, portfolios_list, portfolio_actions, close_confirm,
    replace_asset_kb,
)
from bot.states import (
    WAIT_API_KEY, WAIT_API_SECRET, WAIT_PASSPHRASE,
    WAIT_SYMBOLS, WAIT_TOTAL_AMOUNT, WAIT_ALLOCATIONS,
    WAIT_REBALANCE_MODE, WAIT_TIME_INTERVAL, WAIT_THRESHOLD_PCT, WAIT_CONFIRM,
    WAIT_REPLACE_NEW_SYMBOL, WAIT_REPLACE_CONFIRM,
)
from trading.rebalance_engine import get_engine, PortfolioConfig, AssetConfig
from trading.bitget_client import get_bitget, invalidate_credentials_cache
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
    client = get_bitget()
    api_status = (
        "🟢 <b>مفاتيح API:</b> مُتصلة" if await asyncio.to_thread(client.has_credentials)
        else "🔴 <b>مفاتيح API:</b> غير مُتصلة"
    )
    await _reply(update,
        "✨ <b>بوت إعادة توازن المحفظة</b> ✨\n"
        "<code>─────────────────────────</code>\n"
        "🤖 يدير محفظتك على <b>BitGet</b> ويعيد توازنها تلقائياً.\n\n"
        f"📊 <b>حالة الاتصال:</b>\n{api_status}\n\n"
        "👇 اختر من القائمة:",
        main_menu())


async def cb_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


# ── API setup ─────────────────────────────────────────────────────────────────

async def cb_setup_api(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not _authorized(update):
        await _deny(update)
        return ConversationHandler.END
    await _reply(update,
        "🔑 <b>إعداد مفاتيح BitGet API</b>\n\n"
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
            "✅ <b>تم حفظ المفاتيح والتحقق بنجاح!</b>",
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
    client = get_bitget()
    if not await asyncio.to_thread(client.has_credentials):
        await _reply(update,
            "⚠️ يجب إعداد مفاتيح API أولاً.\nاضغط ⚙️ إعدادات API.",
            main_menu())
        return ConversationHandler.END
    ctx.user_data.clear()
    await _reply(update,
        "🆕 <b>إنشاء محفظة إعادة توازن</b>\n\n"
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

    client = get_bitget()

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
        await update.message.reply_text("❌ لم أجد أي عملة على BitGet. حاول مجدداً:")
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
    lines = [
        "📋 <b>ملخص المحفظة — تأكيد الإنشاء</b>",
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
    )
    engine = get_engine()
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
        })

    lines = [f"📊 <b>المحافظ النشطة ({len(display)})</b>\n"]
    for d in display:
        lines.append(
            f"• <code>{d['id'][:8]}</code> — {d['asset_count']} عملة — "
            f"{d['total_investment']:.0f} USDT"
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
    client = get_bitget()
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
        try:
            res = await asyncio.to_thread(client.liquidate_wallet)
        except Exception as e:
            res = {"cancelled_orders": 0, "sold": [], "errors": [str(e)], "skipped": []}
    finally:
        resume_monitor()

    lines = [f"✅ أُغلقت {closed} محفظة"]
    lines.append(f"🚫 أوامر ملغاة: {res.get('cancelled_orders', 0)}")
    if res.get("sold"):
        lines.append("💹 مبيعات:")
        for s in res["sold"][:10]:
            lines.append(f"  • {_html.escape(str(s))}")
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
    client = get_bitget()
    try:
        res = await asyncio.to_thread(client.liquidate_wallet)
    except Exception as e:
        await _reply(update, f"❌ {e}", back_main())
        return
    lines = ["✅ اكتملت التصفية", f"🚫 أوامر: {res.get('cancelled_orders',0)}"]
    for s in res.get("sold", [])[:10]:
        lines.append(f"  • {_html.escape(str(s))}")
    await _reply(update, "\n".join(lines), back_main())


# ── Balance / PnL ─────────────────────────────────────────────────────────────

async def cb_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return await _deny(update)
    client = get_bitget()
    try:
        balances = await asyncio.to_thread(client.get_account_balance)
        prices = await asyncio.to_thread(client.get_all_tickers)
    except Exception as e:
        await _reply(update, f"❌ {e}", back_main())
        return
    lines = ["🏦 <b>رصيد المحفظة</b>\n"]
    total_usdt = 0.0
    for item in balances:
        coin = (item.get("coin") or item.get("currency") or item.get("asset") or "").upper()
        avail = float(item.get("available") or item.get("availableBalance") or item.get("free") or 0)
        if avail <= 0:
            continue
        if coin == "USDT":
            val = avail
        else:
            val = avail * prices.get(coin, 0)
        if val < 0.5:
            continue
        total_usdt += val
        lines.append(f"  • <code>{coin}</code>: {avail:.6g} ≈ {val:.2f} USDT")
    lines.append(f"\n💵 الإجمالي التقريبي: <b>{total_usdt:.2f} USDT</b>")
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


# ── Cancel command ────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("تم الإلغاء.", reply_markup=main_menu())
    return ConversationHandler.END


# ── Build application ─────────────────────────────────────────────────────────

def build_application(app) -> None:
    api_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_setup_api, pattern="^setup_api$")],
        states={
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

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(api_conv)
    app.add_handler(new_conv)
    app.add_handler(replace_conv)

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
