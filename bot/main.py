"""
نقطة تشغيل البوت. بيعمل loop على كل الأزواج المحددة في SYMBOLS،
يجيب الشموع، يحسب الإشارة، ولو فيه دخول يطبق إدارة المخاطر وينفذ الأمر.
بوت تليجرام بيشتغل في الخلفية بيدي تحكم كامل: إيقاف/تشغيل، رصيد، مراكز، إغلاق طارئ...إلخ.
"""
import logging
import time
import sys

from .config import Config
from .exchange import BitgetExchange
from .strategy import generate_signal
from .risk import RiskManager
from .database import Database
from .state import shared_state
from .telegram_control import TelegramController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("main")


def run():
    Config.validate()
    logger.info(f"بدء البوت | DRY_RUN={Config.DRY_RUN} | الأزواج: {Config.SYMBOLS}")

    exchange = BitgetExchange()
    risk = RiskManager()
    db = Database()

    telegram = TelegramController(exchange, db, risk)
    telegram.start_in_background()
    time.sleep(2)  # مهلة بسيطة لبدء بوت تليجرام قبل أول إشعار
    telegram.notify(
        "🚀 البوت اشتغل.\n"
        f"الوضع: {'🧪 تجربة (Dry Run)' if shared_state.is_dry_run() else '💰 تداول حقيقي'}\n"
        f"الأزواج: {', '.join(Config.SYMBOLS)}\n"
        "اكتب /help لعرض كل الأوامر."
    )

    exchange.load_markets()
    for symbol in Config.SYMBOLS:
        exchange.set_leverage(symbol, Config.LEVERAGE)

    while True:
        try:
            balance = exchange.fetch_balance_usdt()

            if shared_state.is_paused():
                logger.info("البوت في وضع الإيقاف المؤقت (paused) - مفيش فحص لصفقات جديدة.")
                time.sleep(Config.POLL_INTERVAL_SECONDS)
                continue

            if not risk.trading_allowed(balance):
                telegram.notify("🛑 تم إيقاف التداول تلقائياً: تم تجاوز حد الخسارة اليومي المسموح.")
                time.sleep(Config.POLL_INTERVAL_SECONDS * 4)
                continue

            open_positions = exchange.fetch_open_positions(Config.SYMBOLS)

            for symbol in Config.SYMBOLS:
                ohlcv = exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=200)
                signal = generate_signal(ohlcv)
                db.log_signal(symbol, signal.action, signal.price, signal.reason)

                if signal.action == "hold":
                    logger.info(f"{symbol}: hold ({signal.reason})")
                    continue

                if not risk.can_open_new_position(len(open_positions)):
                    logger.info(f"{symbol}: تجاهل الإشارة - وصلنا للحد الأقصى للمراكز المفتوحة.")
                    continue

                amount = risk.position_size(balance, signal.price, signal.stop_loss)
                if amount <= 0:
                    logger.warning(f"{symbol}: حجم صفقة غير صالح، تم التجاهل.")
                    continue

                logger.info(
                    f"{symbol}: إشارة {signal.action.upper()} | سعر={signal.price:.4f} "
                    f"SL={signal.stop_loss:.4f} TP={signal.take_profit:.4f} | حجم={amount}"
                )

                order = exchange.create_market_order(symbol, signal.action, amount)
                trade_id = db.open_trade(
                    symbol, signal.action, amount, signal.price,
                    signal.stop_loss, signal.take_profit, signal.reason, shared_state.is_dry_run(),
                )

                opposite = "sell" if signal.action == "buy" else "buy"
                exchange.create_stop_order(symbol, opposite, amount, signal.stop_loss, order_type="stop")
                exchange.create_stop_order(symbol, opposite, amount, signal.take_profit, order_type="take_profit")

                logger.info(f"{symbol}: تم تنفيذ صفقة #{trade_id} | order={order}")
                telegram.notify(
                    f"📈 صفقة جديدة #{trade_id}\n"
                    f"{symbol} | {signal.action.upper()}\n"
                    f"سعر الدخول: {signal.price:.4f}\n"
                    f"SL: {signal.stop_loss:.4f} | TP: {signal.take_profit:.4f}\n"
                    f"الحجم: {amount}\n"
                    f"السبب: {signal.reason}"
                )

            time.sleep(Config.POLL_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("إيقاف يدوي للبوت.")
            sys.exit(0)
        except Exception as e:
            logger.exception(f"خطأ غير متوقع في الحلقة الرئيسية: {e}")
            telegram.notify(f"⚠️ خطأ في البوت: {e}")
            time.sleep(Config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
