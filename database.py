"""database.py — SQLAlchemy models and DB helper functions."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, Integer, String, Text,
    create_engine, select, update, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Fix Railway postgres:// → postgresql://
_url = DATABASE_URL
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

engine = create_engine(_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    close_reason: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    signal_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    bitget_order_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    risk_percent: Mapped[float] = mapped_column(Float, default=1.0)
    active_pairs: Mapped[str] = mapped_column(Text, default="BTC/USDT,ETH/USDT")
    # ── New: fixed trade amount (USDT) and dynamic timeframe ──────────────────
    trade_amount_usdt: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=None)
    timeframe: Mapped[str] = mapped_column(String(10), default="15m")


class BotLog(Base):
    __tablename__ = "bot_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        settings = s.get(BotSettings, 1)
        if settings is None:
            from config import DEFAULT_RISK_PERCENT, DEFAULT_PAIRS, TIMEFRAME
            s.add(BotSettings(
                id=1,
                trading_enabled=True,
                risk_percent=DEFAULT_RISK_PERCENT,
                active_pairs=",".join(DEFAULT_PAIRS),
                trade_amount_usdt=None,
                timeframe=TIMEFRAME,
            ))
            s.commit()
        else:
            # Migrate existing rows that may lack the new columns
            changed = False
            if not hasattr(settings, "timeframe") or settings.timeframe is None:
                settings.timeframe = "15m"
                changed = True
            if changed:
                s.commit()
    logger.info("Database initialised")


# ── Settings helpers ──────────────────────────────────────────────────────────

def get_settings() -> BotSettings:
    with SessionLocal() as s:
        return s.get(BotSettings, 1)


def set_trading_enabled(enabled: bool) -> None:
    with SessionLocal() as s:
        s.execute(update(BotSettings).where(BotSettings.id == 1).values(trading_enabled=enabled))
        s.commit()


def set_risk_percent(pct: float) -> None:
    with SessionLocal() as s:
        s.execute(update(BotSettings).where(BotSettings.id == 1).values(risk_percent=pct))
        s.commit()


def set_trade_amount(amount: Optional[float]) -> None:
    """Set fixed USDT amount per trade. Pass None to revert to risk-% sizing."""
    with SessionLocal() as s:
        s.execute(update(BotSettings).where(BotSettings.id == 1).values(trade_amount_usdt=amount))
        s.commit()


def set_timeframe(tf: str) -> None:
    """Set the trading timeframe (e.g. '1m', '5m', '15m', '1h', '4h', '1d')."""
    with SessionLocal() as s:
        s.execute(update(BotSettings).where(BotSettings.id == 1).values(timeframe=tf))
        s.commit()


def set_active_pairs(pairs: list[str]) -> None:
    with SessionLocal() as s:
        s.execute(update(BotSettings).where(BotSettings.id == 1).values(active_pairs=",".join(pairs)))
        s.commit()


def get_active_pairs() -> list[str]:
    cfg = get_settings()
    return [p.strip() for p in cfg.active_pairs.split(",") if p.strip()]


# ── Trade helpers ─────────────────────────────────────────────────────────────

def save_trade(trade: Trade) -> Trade:
    with SessionLocal() as s:
        s.add(trade)
        s.commit()
        s.refresh(trade)
        return trade


def get_open_trades() -> list[Trade]:
    with SessionLocal() as s:
        return list(s.scalars(select(Trade).where(Trade.is_closed == False)).all())


def get_open_trade_for_symbol(symbol: str) -> Optional[Trade]:
    with SessionLocal() as s:
        return s.scalars(
            select(Trade).where(Trade.symbol == symbol, Trade.is_closed == False).limit(1)
        ).first()


def close_trade(trade_id: int, exit_price: float, pnl: float, reason: str) -> None:
    with SessionLocal() as s:
        trade = s.get(Trade, trade_id)
        if trade:
            trade.exit_price = exit_price
            trade.pnl = pnl
            trade.is_closed = True
            trade.close_reason = reason
            trade.closed_at = datetime.now(timezone.utc)
            s.commit()


def get_today_stats() -> dict:
    today = datetime.now(timezone.utc).date()
    with SessionLocal() as s:
        result = s.execute(
            select(
                func.count(Trade.id).label("count"),
                func.coalesce(func.sum(Trade.pnl), 0.0).label("pnl"),
            ).where(
                Trade.is_closed == True,
                func.date(Trade.closed_at) == today,
            )
        ).one()
        return {"count": result.count, "pnl": float(result.pnl)}


# ── Log helper ────────────────────────────────────────────────────────────────

def db_log(level: str, message: str) -> None:
    try:
        with SessionLocal() as s:
            s.add(BotLog(level=level, message=message[:1000]))
            s.commit()
    except Exception:
        pass
