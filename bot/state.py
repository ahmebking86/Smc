"""
حالة مشتركة (Thread-safe) بين حلقة التداول الرئيسية وبوت تليجرام،
عشان أوامر زي /pause أو /setrisk تأثر على البوت وهو شغال لحظياً.
"""
import threading
from datetime import datetime, timezone

from .config import Config


class SharedState:
    def __init__(self):
        self._lock = threading.Lock()
        self.dry_run = Config.DRY_RUN
        self.paused = False
        self.risk_per_trade_pct = None  # None = استخدم القيمة من Config
        self.start_time = datetime.now(timezone.utc)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "dry_run": self.dry_run,
                "paused": self.paused,
                "risk_per_trade_pct": self.risk_per_trade_pct,
            }

    def set_dry_run(self, val: bool):
        with self._lock:
            self.dry_run = val

    def is_dry_run(self) -> bool:
        with self._lock:
            return self.dry_run

    def set_paused(self, val: bool):
        with self._lock:
            self.paused = val

    def is_paused(self) -> bool:
        with self._lock:
            return self.paused

    def set_risk_pct(self, val: float):
        with self._lock:
            self.risk_per_trade_pct = val

    def get_risk_pct(self) -> float:
        with self._lock:
            return self.risk_per_trade_pct if self.risk_per_trade_pct is not None else Config.RISK_PER_TRADE_PCT


shared_state = SharedState()
