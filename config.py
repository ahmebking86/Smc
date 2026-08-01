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

# ── App ───────────────────────────────────────────────────────────────────────
MONITOR_INTERVAL   = 30       # seconds between rebalance checks
MIN_ORDER_USDT     = 1.5      # minimum USDT for any market order (safety buffer)
MAX_ASSETS         = 20       # max coins in one portfolio
