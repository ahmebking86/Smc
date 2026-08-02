"""
Shared Database Models - Cluster Spot Guard
(Same models as Watcher - keep in sync)
"""

from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, JSON
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool
import enum
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/cluster_guard")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, poolclass=NullPool, echo=False)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


class SignalType(str, enum.Enum):
    CLUSTER_EXIT = "cluster_exit"
    CLUSTER_ACCUMULATION = "cluster_accumulation"


class SignalStatus(str, enum.Enum):
    NEW = "new"
    PROCESSED = "processed"
    IGNORED = "ignored"
    ACTED = "acted"


class TradeStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True)
    address = Column(String(128), unique=True, nullable=False, index=True)
    chain = Column(String(32), nullable=False)
    label = Column(String(128))
    category = Column(String(64))
    win_rate = Column(Float, default=0.0)
    notes = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ClusterSignal(Base):
    __tablename__ = "cluster_signals"

    id = Column(Integer, primary_key=True)
    signal_type = Column(String(32), nullable=False)
    token_symbol = Column(String(32))
    token_address = Column(String(128))
    chain = Column(String(32))
    wallet_count = Column(Integer, default=0)
    total_amount_usd = Column(Float, default=0.0)
    conviction_score = Column(Float, default=0.0)
    wallets_involved = Column(JSON)
    raw_data = Column(JSON)
    status = Column(String(32), default=SignalStatus.NEW.value)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer, nullable=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), default="buy")
    entry_price = Column(Float)
    quantity = Column(Float)
    usdt_size = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    status = Column(String(16), default=TradeStatus.OPEN.value)
    mode = Column(String(16), default="paper")
    exit_reason = Column(String(64), nullable=True)
    entry_time = Column(DateTime, default=datetime.utcnow)
    exit_time = Column(DateTime, nullable=True)


class SystemState(Base):
    __tablename__ = "system_state"

    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True)
    value = Column(String(256))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_session():
    return SessionLocal()


def is_paused() -> bool:
    session = get_session()
    try:
        row = session.query(SystemState).filter_by(key="paused").first()
        return row is not None and row.value == "true"
    finally:
        session.close()


def set_paused(value: bool):
    session = get_session()
    try:
        row = session.query(SystemState).filter_by(key="paused").first()
        if row:
            row.value = "true" if value else "false"
        else:
            session.add(SystemState(key="paused", value="true" if value else "false"))
        session.commit()
    finally:
        session.close()
