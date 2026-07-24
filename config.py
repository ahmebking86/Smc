"""config.py — All settings from environment variables."""
import os

# BUG FIX: load_dotenv() was missing — .env files never loaded for local dev.
# python-dotenv was in requirements.txt but never called.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed — production env vars come from the host


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID: int = int(_require("TELEGRAM_CHAT_ID"))

# ── Bitget ────────────────────────────────────────────────────────────────────
BITGET_API_KEY: str = _require("BITGET_API_KEY")
BITGET_SECRET: str = _require("BITGET_SECRET")
BITGET_PASSPHRASE: str = _require("BITGET_PASSPHRASE")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL: str = _require("DATABASE_URL")

# ── Bot defaults (overridden by DB settings at runtime) ───────────────────────
DEFAULT_RISK_PERCENT: float = float(os.environ.get("DEFAULT_RISK_PCT", "1.0"))
DEFAULT_PAIRS: list[str] = [
    p.strip() for p in os.environ.get("DEFAULT_PAIRS", "BTC/USDT,ETH/USDT").split(",")
]

# ── Strategy ──────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS: int = int(os.environ.get("SCAN_INTERVAL_SECONDS", "60"))
TIMEFRAME: str = os.environ.get("TIMEFRAME", "15m")   # default only — DB overrides at runtime
KLINE_LIMIT: int = int(os.environ.get("KLINE_LIMIT", "200"))

# ── Health server ─────────────────────────────────────────────────────────────
PORT: int = int(os.environ.get("PORT", "8080"))
