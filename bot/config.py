"""
كل الإعدادات بتتحمل من متغيرات البيئة (Environment Variables).
في Railway: Project -> Variables -> ضيف كل متغير هنا كـ Secret.
محدش يكتب أي مفتاح API جوا الكود مباشرة.
"""
import os


def _get_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


class Config:
    # ---- Bitget API credentials (سرية - من Railway Variables) ----
    BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
    BITGET_API_SECRET = os.getenv("BITGET_API_SECRET", "")
    BITGET_API_PASSPHRASE = os.getenv("BITGET_API_PASSPHRASE", "")

    # ---- وضع التشغيل ----
    # DRY_RUN=true -> البوت يحلل ويطبع القرارات بدون تنفيذ أوامر حقيقية
    # DRY_RUN=false -> تنفيذ حقيقي على الحساب (فلوس حقيقية)
    DRY_RUN = _get_bool("DRY_RUN", default=False)

    # ---- الأزواج (comma separated), مثال: "BTC/USDT:USDT,ETH/USDT:USDT" ----
    SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "BTC/USDT:USDT").split(",") if s.strip()]

    # ---- نوع السوق: swap (فيوتشرز) أو spot ----
    MARKET_TYPE = os.getenv("MARKET_TYPE", "swap")  # "swap" or "spot"

    # ---- الفريم الزمني للسكالبينج ----
    TIMEFRAME = os.getenv("TIMEFRAME", "1m")

    # ---- إعدادات الاستراتيجية (EMA + RSI + ATR) ----
    EMA_FAST = _get_int("EMA_FAST", 9)
    EMA_SLOW = _get_int("EMA_SLOW", 21)
    RSI_PERIOD = _get_int("RSI_PERIOD", 14)
    RSI_OVERBOUGHT = _get_float("RSI_OVERBOUGHT", 70)
    RSI_OVERSOLD = _get_float("RSI_OVERSOLD", 30)
    ATR_PERIOD = _get_int("ATR_PERIOD", 14)
    ATR_SL_MULT = _get_float("ATR_SL_MULT", 1.2)   # مضاعف الستوب لوس
    ATR_TP_MULT = _get_float("ATR_TP_MULT", 1.8)   # مضاعف جني الأرباح

    # ---- إدارة المخاطر (مهم جداً في التداول الحقيقي) ----
    RISK_PER_TRADE_PCT = _get_float("RISK_PER_TRADE_PCT", 0.5)   # % من رأس المال لكل صفقة
    MAX_DAILY_LOSS_PCT = _get_float("MAX_DAILY_LOSS_PCT", 3.0)   # وقف البوت لو الخسارة اليومية تعدت النسبة دي
    MAX_OPEN_POSITIONS = _get_int("MAX_OPEN_POSITIONS", 2)
    LEVERAGE = _get_int("LEVERAGE", 3)

    # ---- تكرار فحص السوق (بالثواني) ----
    POLL_INTERVAL_SECONDS = _get_int("POLL_INTERVAL_SECONDS", 15)

    # ---- قاعدة البيانات (Railway Postgres) ----
    DATABASE_URL = os.getenv("DATABASE_URL", "")  # Railway بيحطها تلقائي لما تضيف Postgres plugin

    # ---- بوت تليجرام للتحكم (اختياري لكن مطلوب حسب طلبك) ----
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # آيديات المستخدمين المصرح لهم (Chat ID) - مفصولة بفاصلة لو أكتر من شخص
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    @classmethod
    def validate(cls):
        missing = []
        if not cls.DRY_RUN:
            if not cls.BITGET_API_KEY:
                missing.append("BITGET_API_KEY")
            if not cls.BITGET_API_SECRET:
                missing.append("BITGET_API_SECRET")
            if not cls.BITGET_API_PASSPHRASE:
                missing.append("BITGET_API_PASSPHRASE")
        if not cls.DATABASE_URL:
            missing.append("DATABASE_URL")
        if not cls.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not cls.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise RuntimeError(
                f"متغيرات بيئة ناقصة: {', '.join(missing)}. "
                f"ضيفها في Railway -> Variables."
            )
