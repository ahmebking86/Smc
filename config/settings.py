"""
Cluster Judge - Configuration
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TG_ADMIN_IDS = [int(x) for x in os.getenv("TG_ADMIN_IDS", "").split(",") if x.strip()]

# MEXC
MEXC_API_KEY = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET = os.getenv("MEXC_SECRET", "")

# Database (Shared with Watcher)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/cluster_guard")

# Capital & Risk
CAPITAL = float(os.getenv("CAPITAL", "300"))
MAX_POSITION_USDT = float(os.getenv("MAX_POSITION_USDT", "90"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.2"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "4.5"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
MODE = os.getenv("MODE", "paper").lower()

# Judge Logic
MIN_CONVICTION_SCORE = int(os.getenv("MIN_CONVICTION_SCORE", "65"))

# Safety
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PAUSE_ON_START = os.getenv("PAUSE_ON_START", "false").lower() == "true"
ENABLE_REAL_TRADING = MODE == "real"
