import os
import sys
from dotenv import load_dotenv

load_dotenv()

def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"[FATAL] المتغير '{name}' غير معرّف أو فارغ في بيئة التشغيل.", file=sys.stderr)
        print(f"        أضفه في Railway → Variables أو في ملف .env المحلي.", file=sys.stderr)
        sys.exit(1)
    return val

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = _require("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(_require("TELEGRAM_CHAT_ID"))

# ── PostgreSQL (Railway) ───────────────────────────────────────────────────────
DATABASE_URL = _require("DATABASE_URL")

# ── BitGet API ────────────────────────────────────────────────────────────────
BITGET_API_KEY    = os.getenv("BITGET_API_KEY", "")
BITGET_API_SECRET = os.getenv("BITGET_API_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")
BITGET_BASE_URL   = "https://api.bitget.com"

# ── App ───────────────────────────────────────────────────────────────────────
MONITOR_INTERVAL   = 5        # seconds between price checks (fast tracking)
MIN_ENTRY_AMOUNT   = 1.0      # minimum USDT per grid level
