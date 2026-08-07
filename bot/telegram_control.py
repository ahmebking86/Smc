"""
تحكم كامل بالبوت عبر تليجرام.
الأوامر متاحة فقط للـ Chat ID المحدد في TELEGRAM_CHAT_ID (حماية من أي حد تاني يتحكم في حسابك).
"""
import asyncio
import logging
import threading
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import Config
from .state import shared_state

logger = logging.getLogger("telegram")


def _allowed_chat_ids() -> list[str]:
    return [c.strip() for c in Config.TELEGRAM_CHAT_ID.split(",") if c.strip()]


class TelegramController:
    def __init__(self, exchange, db, risk):
        self.exchange = exchange
        self.db = db
        self.risk = risk
        self.app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._register_handlers()

    # ---------- تسجيل الأوامر ----------
    def _register_handlers(self):
        cmds = {
            "start": self.cmd_help,
            "help": self.cmd_help,
            "status": self.cmd_status,
            "balance": self.cmd_balance,
            "positions": self.cmd_positions,
            "trades": self.cmd_trades,
            "pnl": self.cmd_pnl,
            "pause": self.cmd_pause,
            "resume": self.cmd_resume,
            "dryrun_on": self.cmd_dryrun_on,
            "dryrun_off": self.cmd_dryrun_off,
            "setrisk": self.cmd_setrisk,
            "closeall": self.cmd_closeall,
        }
        for name, handler in cmds.items():
            self.app.add_handler(CommandHandler(name, handler))

    async def _guard(self, update: Update) -> bool:
        if str(update.effective_chat.id) not in _allowed_chat_ids():
            await update.message.reply_text("🚫 غير مصرح لك باستخدام هذا البوت.")
            logger.warning(f"محاولة دخول غير مصرح بها من chat_id={update.effective_chat.id}")
            return False
        return True

    # ---------- الأوامر ----------
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        text = (
            "🤖 *أوامر التحكم في بوت السكالبينج*\n\n"
            "/status - حالة البوت الحالية\n"
            "/balance - الرصيد المتاح (USDT)\n"
            "/positions - المراكز المفتوحة حالياً\n"
            "/trades - آخر الصفقات المفتوحة المسجلة\n"
            "/pnl - أداء اليوم (%)\n"
            "/pause - إيقاف فتح صفقات جديدة (الحالية تفضل شغالة)\n"
            "/resume - استئناف فتح صفقات جديدة\n"
            "/dryrun\\_on - تفعيل وضع التجربة (بدون تنفيذ حقيقي)\n"
            "/dryrun\\_off - تفعيل التداول الحقيقي فوراً\n"
            "/setrisk <نسبة> - تعديل % المخاطرة لكل صفقة، مثال: /setrisk 0.5\n"
            "/closeall confirm - إغلاق كل المراكز المفتوحة فوراً بسعر السوق"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        s = shared_state.snapshot()
        uptime = datetime.now(timezone.utc) - shared_state.start_time
        text = (
            "⚙️ *حالة البوت*\n"
            f"التشغيل: {'⏸ متوقف مؤقتاً عن فتح صفقات جديدة' if s['paused'] else '▶️ يعمل بشكل طبيعي'}\n"
            f"الوضع: {'🧪 تجربة (Dry Run)' if s['dry_run'] else '💰 تداول حقيقي'}\n"
            f"الأزواج: {', '.join(Config.SYMBOLS)}\n"
            f"الفريم: {Config.TIMEFRAME}\n"
            f"% المخاطرة/صفقة: {shared_state.get_risk_pct()}%\n"
            f"مدة التشغيل: {str(uptime).split('.')[0]}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        bal = self.exchange.fetch_balance_usdt()
        await update.message.reply_text(f"💰 الرصيد المتاح: {bal:.2f} USDT")

    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        positions = self.exchange.fetch_open_positions(Config.SYMBOLS)
        if not positions:
            await update.message.reply_text("لا توجد مراكز مفتوحة حالياً.")
            return
        lines = []
        for p in positions:
            symbol = p.get("symbol", "?")
            side = p.get("side", "?")
            contracts = p.get("contracts", "?")
            upnl = p.get("unrealizedPnl", "?")
            lines.append(f"📌 {symbol} | {side} | حجم={contracts} | ربح/خسارة غير محقق={upnl}")
        await update.message.reply_text("\n".join(lines))

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        trades = self.db.open_trades()
        if not trades:
            await update.message.reply_text("لا توجد صفقات مفتوحة مسجلة في قاعدة البيانات.")
            return
        lines = [
            f"#{t['id']} {t['symbol']} {t['side']} @ {t['entry_price']} "
            f"(SL={t['stop_loss']}, TP={t['take_profit']})"
            for t in trades
        ]
        await update.message.reply_text("\n".join(lines))

    async def cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        await update.message.reply_text(f"📊 أداء اليوم الحالي: {self.risk.daily_pnl_pct:.2f}%")

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        shared_state.set_paused(True)
        await update.message.reply_text("⏸ تم إيقاف فتح صفقات جديدة. الصفقات المفتوحة حالياً هتفضل شغالة زي ما هي.")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        shared_state.set_paused(False)
        await update.message.reply_text("▶️ تم استئناف التداول وفتح صفقات جديدة.")

    async def cmd_dryrun_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        shared_state.set_dry_run(True)
        await update.message.reply_text("🧪 تم تفعيل وضع التجربة. مفيش أي أوامر حقيقية هتتنفذ دلوقتي.")

    async def cmd_dryrun_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        shared_state.set_dry_run(False)
        await update.message.reply_text("💰 تم تفعيل التداول الحقيقي. الأوامر هتتنفذ فعلياً على حسابك من اللحظة دي.")

    async def cmd_setrisk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        if not context.args:
            await update.message.reply_text("استخدم: /setrisk 0.5  (يعني 0.5% من الرصيد كمخاطرة لكل صفقة)")
            return
        try:
            pct = float(context.args[0])
            if pct <= 0 or pct > 20:
                await update.message.reply_text("⚠️ القيمة غير منطقية. اختر نسبة بين 0.01 و 20.")
                return
            shared_state.set_risk_pct(pct)
            await update.message.reply_text(f"✅ تم ضبط % المخاطرة لكل صفقة إلى {pct}%")
        except ValueError:
            await update.message.reply_text("قيمة غير صحيحة. استخدم رقم مثل: /setrisk 0.5")

    async def cmd_closeall(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._guard(update):
            return
        if not context.args or context.args[0].lower() != "confirm":
            await update.message.reply_text(
                "⚠️ الأمر ده هيقفل كل المراكز المفتوحة فوراً بسعر السوق.\nللتأكيد اكتب: /closeall confirm"
            )
            return
        positions = self.exchange.fetch_open_positions(Config.SYMBOLS)
        if not positions:
            await update.message.reply_text("مفيش مراكز مفتوحة أصلاً.")
            return
        closed = 0
        for p in positions:
            try:
                symbol = p.get("symbol")
                side = p.get("side")
                amount = abs(float(p.get("contracts") or 0))
                if amount > 0:
                    self.exchange.close_position_market(symbol, side, amount)
                    closed += 1
            except Exception as e:
                logger.error(f"فشل إغلاق مركز: {e}")
        await update.message.reply_text(f"✅ تم إغلاق {closed} مركز بأمر يدوي.")

    # ---------- إشعارات تلقائية من حلقة التداول ----------
    def notify(self, text: str):
        """يُستدعى من حلقة التداول الرئيسية لإرسال إشعار (صفقة جديدة، إغلاق، تحذير...)."""
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send_to_all(text), self._loop)
        except Exception as e:
            logger.error(f"فشل جدولة إشعار تليجرام: {e}")

    async def _send_to_all(self, text: str):
        for chat_id in _allowed_chat_ids():
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                logger.error(f"فشل إرسال رسالة لـ {chat_id}: {e}")

    # ---------- تشغيل البوت في الخلفية ----------
    async def _run_async(self):
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("✅ بوت تليجرام شغال ويستقبل الأوامر.")
        stop_event = asyncio.Event()
        await stop_event.wait()  # يفضل شغال للأبد

    def start_in_background(self):
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._run_async())
            except Exception as e:
                logger.exception(f"توقف بوت تليجرام بخطأ: {e}")

        self._thread = threading.Thread(target=_run, daemon=True, name="telegram-controller")
        self._thread.start()
