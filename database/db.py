"""
PostgreSQL database layer — portfolios, assets, trades, settings.
Supports Bitget + MEXC (exchange column on portfolios).
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

from config import DATABASE_URL

logger = logging.getLogger(__name__)

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(1, 15, DATABASE_URL)
            logger.info("✅ اتصال PostgreSQL تم بنجاح.")
        except Exception as exc:
            logger.critical("❌ فشل الاتصال بـ PostgreSQL: %s", exc)
            raise RuntimeError(f"PostgreSQL connection failed: {exc}") from exc
    return _pool


def _reset_pool() -> None:
    """Close and recreate the pool (used after connection errors)."""
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
        except Exception:
            pass
        _pool = None
    _get_pool()


def _exec(
    sql: str,
    params: tuple | None = None,
    fetch: str = "none",
    retries: int = 2,
) -> Any:
    last_err = None
    for attempt in range(retries + 1):
        pool = _get_pool()
        conn = None
        try:
            conn = pool.getconn()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                conn.commit()
                if fetch == "one":
                    row = cur.fetchone()
                    return dict(row) if row else None
                elif fetch == "all":
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
                return None
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            last_err = e
            logger.warning("DB connection error (attempt %d): %s", attempt + 1, e)
            if conn:
                try:
                    pool.putconn(conn, close=True)
                except Exception:
                    pass
            _reset_pool()
            if attempt < retries:
                continue
            raise
        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn is not None:
                try:
                    pool.putconn(conn)
                except Exception:
                    pass
    if last_err:
        raise last_err


_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS portfolios (
    id                    TEXT        PRIMARY KEY,
    total_investment      NUMERIC     NOT NULL,
    rebalance_mode        TEXT        NOT NULL,
    interval_hours        NUMERIC     NOT NULL DEFAULT 0,
    threshold_pct         NUMERIC     NOT NULL DEFAULT 0,
    status                TEXT        NOT NULL DEFAULT 'active',
    total_pnl             NUMERIC     NOT NULL DEFAULT 0,
    last_rebalance_at     TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at             TIMESTAMPTZ,
    exchange              TEXT        NOT NULL DEFAULT 'bitget'
);

CREATE TABLE IF NOT EXISTS portfolio_assets (
    id              TEXT        PRIMARY KEY,
    portfolio_id    TEXT        NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    symbol          TEXT        NOT NULL,
    target_pct      NUMERIC     NOT NULL,
    initial_qty     NUMERIC     NOT NULL DEFAULT 0,
    status          TEXT        NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rebalance_trades (
    id              TEXT        PRIMARY KEY,
    portfolio_id    TEXT        NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    symbol          TEXT        NOT NULL,
    side            TEXT        NOT NULL,
    usdt_amount     NUMERIC     NOT NULL,
    qty             NUMERIC     NOT NULL DEFAULT 0,
    price           NUMERIC     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portfolios_status ON portfolios(status);
CREATE INDEX IF NOT EXISTS idx_assets_portfolio  ON portfolio_assets(portfolio_id);
CREATE INDEX IF NOT EXISTS idx_trades_portfolio  ON rebalance_trades(portfolio_id);
"""


def _ensure_bot_settings_schema(cur) -> None:
    """Detect wrong schema (old flat columns) and recreate bot_settings."""
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'bot_settings'
    """)
    cols = {row[0] for row in cur.fetchall()}
    if not cols:
        return
    expected = {"key", "value"}
    if cols != expected and "key" not in cols:
        logger.warning("bot_settings has wrong schema %s — recreating", cols)
        cur.execute("DROP TABLE IF EXISTS bot_settings CASCADE")
        cur.execute("""
            CREATE TABLE bot_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        logger.info("✅ جدول bot_settings أُنشئ/أُصلح.")
    elif "value" not in cols:
        logger.warning("bot_settings missing 'value' column — recreating")
        cur.execute("DROP TABLE IF EXISTS bot_settings CASCADE")
        cur.execute("""
            CREATE TABLE bot_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        logger.info("✅ جدول bot_settings أُنشئ/أُصلح.")


def _ensure_exchange_column(cur) -> None:
    """Add exchange column to portfolios if missing (migration for existing DBs)."""
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'portfolios' AND column_name = 'exchange'
    """)
    if cur.fetchone() is None:
        cur.execute("""
            ALTER TABLE portfolios
            ADD COLUMN exchange TEXT NOT NULL DEFAULT 'bitget'
        """)
        logger.info("✅ أُضيف عمود exchange لجدول portfolios.")


def init_db() -> None:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLES_SQL)
            _ensure_bot_settings_schema(cur)
            _ensure_exchange_column(cur)
        conn.commit()
        logger.info("✅ قاعدة البيانات جاهزة.")
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.critical("❌ فشل تهيئة قاعدة البيانات: %s", exc)
        raise
    finally:
        pool.putconn(conn)


# ── Portfolios ────────────────────────────────────────────────────────────────

def create_portfolio(data: dict) -> dict:
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    sql = f"INSERT INTO portfolios ({cols}) VALUES ({placeholders}) RETURNING *"
    row = _exec(sql, tuple(data.values()), fetch="one")
    if not row:
        raise RuntimeError(f"create_portfolio failed: {data!r}")
    return row


def get_portfolio(portfolio_id: str) -> Optional[dict]:
    return _exec("SELECT * FROM portfolios WHERE id = %s", (portfolio_id,), fetch="one")


def list_active_portfolios() -> list[dict]:
    return _exec(
        "SELECT * FROM portfolios WHERE status IN ('active','paused') ORDER BY created_at DESC",
        fetch="all",
    ) or []


def update_portfolio(portfolio_id: str, data: dict) -> dict:
    if not data:
        return get_portfolio(portfolio_id) or {}
    set_clause = ", ".join(f"{k} = %s" for k in data.keys())
    sql = f"UPDATE portfolios SET {set_clause} WHERE id = %s RETURNING *"
    row = _exec(sql, (*data.values(), portfolio_id), fetch="one")
    return row or {}


def close_portfolio(portfolio_id: str, pnl: float) -> None:
    update_portfolio(portfolio_id, {
        "status":    "closed",
        "total_pnl": pnl,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Assets ────────────────────────────────────────────────────────────────────

def create_asset(data: dict) -> dict:
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    sql = f"INSERT INTO portfolio_assets ({cols}) VALUES ({placeholders}) RETURNING *"
    row = _exec(sql, tuple(data.values()), fetch="one")
    if not row:
        raise RuntimeError(f"create_asset failed: {data!r}")
    return row


def get_portfolio_assets(portfolio_id: str) -> list[dict]:
    return _exec(
        "SELECT * FROM portfolio_assets WHERE portfolio_id = %s AND status = 'active' ORDER BY target_pct DESC",
        (portfolio_id,), fetch="all",
    ) or []


def update_asset(asset_id: str, data: dict) -> None:
    if not data:
        return
    set_clause = ", ".join(f"{k} = %s" for k in data.keys())
    _exec(f"UPDATE portfolio_assets SET {set_clause} WHERE id = %s", (*data.values(), asset_id))


def deactivate_asset(asset_id: str) -> None:
    update_asset(asset_id, {"status": "closed"})


# ── Trades ────────────────────────────────────────────────────────────────────

def create_trade(data: dict) -> dict:
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    sql = f"INSERT INTO rebalance_trades ({cols}) VALUES ({placeholders}) RETURNING *"
    row = _exec(sql, tuple(data.values()), fetch="one")
    if not row:
        raise RuntimeError(f"create_trade failed: {data!r}")
    return row


def get_portfolio_trades(portfolio_id: str) -> list[dict]:
    return _exec(
        "SELECT * FROM rebalance_trades WHERE portfolio_id = %s ORDER BY created_at DESC",
        (portfolio_id,), fetch="all",
    ) or []


def portfolio_total_pnl(portfolio_id: str) -> float:
    """Approximate PnL: sum of sells - sum of buys (simplified)."""
    row = _exec(
        """
        SELECT
            COALESCE(SUM(CASE WHEN side = 'sell' THEN usdt_amount ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN side = 'buy'  THEN usdt_amount ELSE 0 END), 0)
          AS total
        FROM rebalance_trades WHERE portfolio_id = %s
        """,
        (portfolio_id,), fetch="one",
    )
    return float(row["total"]) if row else 0.0


# ── Settings ──────────────────────────────────────────────────────────────────

def set_setting(key: str, value: str) -> None:
    _exec(
        """
        INSERT INTO bot_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, value),
    )


def get_setting(key: str) -> Optional[str]:
    row = _exec("SELECT value FROM bot_settings WHERE key = %s", (key,), fetch="one")
    return row["value"] if row else None
