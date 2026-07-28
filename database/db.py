"""
PostgreSQL database layer — all CRUD for grid sessions, orders, trades.
Uses psycopg2-binary + ThreadedConnectionPool for thread-safe access.
Tables are created automatically on first startup via init_db().
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

# ── Connection pool ───────────────────────────────────────────────────────────
_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
            logger.info("✅ اتصال PostgreSQL تم بنجاح.")
        except Exception as exc:
            logger.critical("❌ فشل الاتصال بـ PostgreSQL: %s", exc)
            raise RuntimeError(f"PostgreSQL connection failed: {exc}") from exc
    return _pool


def _exec(
    sql: str,
    params: tuple | None = None,
    fetch: str = "none",   # "none" | "one" | "all"
) -> Any:
    """Execute a SQL statement and optionally return rows as dicts."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            conn.commit()
            if fetch == "one":
                row = cur.fetchone()
                return dict(row) if row else None
            elif fetch == "all":
                rows = cur.fetchall()
                return [dict(r) for r in rows]
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── Schema SQL ────────────────────────────────────────────────────────────────
_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS grid_sessions (
    id                TEXT        PRIMARY KEY,
    symbol            TEXT        NOT NULL,
    entry_amount      NUMERIC     NOT NULL,
    upper_pct         NUMERIC     NOT NULL DEFAULT 0,
    lower_pct         NUMERIC     NOT NULL DEFAULT 0,
    grid_count        INTEGER     NOT NULL DEFAULT 0,
    step_pct          NUMERIC     NOT NULL DEFAULT 0,
    levels_per_side   INTEGER     NOT NULL DEFAULT 0,
    lower_limit_price NUMERIC     NOT NULL DEFAULT 0,
    upper_limit_price NUMERIC     NOT NULL DEFAULT 0,
    profit_target     NUMERIC     NOT NULL DEFAULT 0,
    stop_loss         NUMERIC     NOT NULL DEFAULT 0,
    base_price        NUMERIC     NOT NULL,
    upper_price       NUMERIC     NOT NULL,
    lower_price       NUMERIC     NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'active',
    total_pnl         NUMERIC     NOT NULL DEFAULT 0,
    depth             INTEGER     NOT NULL DEFAULT 0,
    parent_id         TEXT        REFERENCES grid_sessions(id),
    trailing_stop     BOOLEAN     NOT NULL DEFAULT FALSE,
    trailing_pct      NUMERIC     NOT NULL DEFAULT 0,
    group_id          TEXT        DEFAULT NULL,
    pump_enabled      BOOLEAN     NOT NULL DEFAULT FALSE,
    pump_detect_pct   NUMERIC     NOT NULL DEFAULT 10,
    pump_trailing_pct NUMERIC     NOT NULL DEFAULT 2,
    pump_budget       NUMERIC     NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at         TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS grid_orders (
    id          TEXT        PRIMARY KEY,
    session_id  TEXT        NOT NULL REFERENCES grid_sessions(id) ON DELETE CASCADE,
    order_id    TEXT,
    side        TEXT        NOT NULL,
    price       NUMERIC     NOT NULL,
    qty         NUMERIC     NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'open',
    entry_price NUMERIC,
    filled_at   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS grid_trades (
    id          TEXT        PRIMARY KEY,
    session_id  TEXT        NOT NULL REFERENCES grid_sessions(id) ON DELETE CASCADE,
    buy_price   NUMERIC     NOT NULL,
    sell_price  NUMERIC     NOT NULL,
    qty         NUMERIC     NOT NULL,
    pnl         NUMERIC     NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON grid_sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_group  ON grid_sessions(group_id);
CREATE INDEX IF NOT EXISTS idx_orders_session  ON grid_orders(session_id);
CREATE INDEX IF NOT EXISTS idx_orders_status   ON grid_orders(status);
CREATE INDEX IF NOT EXISTS idx_trades_session  ON grid_trades(session_id);
"""

# Migrations for databases that were created before the full schema above.
# Every statement is idempotent (ADD COLUMN IF NOT EXISTS).
_MIGRATIONS = [
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS trailing_stop       BOOLEAN  NOT NULL DEFAULT FALSE",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS trailing_pct        NUMERIC  NOT NULL DEFAULT 0",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS group_id            TEXT     DEFAULT NULL",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS step_pct            NUMERIC  NOT NULL DEFAULT 0",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS levels_per_side     INTEGER  NOT NULL DEFAULT 0",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS lower_limit_price   NUMERIC  NOT NULL DEFAULT 0",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS upper_limit_price   NUMERIC  NOT NULL DEFAULT 0",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS pump_enabled        BOOLEAN  NOT NULL DEFAULT FALSE",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS pump_detect_pct     NUMERIC  NOT NULL DEFAULT 10",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS pump_trailing_pct   NUMERIC  NOT NULL DEFAULT 2",
    "ALTER TABLE grid_sessions ADD COLUMN IF NOT EXISTS pump_budget         NUMERIC  NOT NULL DEFAULT 0",
    "ALTER TABLE grid_orders   ADD COLUMN IF NOT EXISTS entry_price         NUMERIC",
    "CREATE INDEX IF NOT EXISTS idx_sessions_group ON grid_sessions(group_id)",
]


def init_db() -> None:
    """Create tables and run migrations. Call once at bot startup."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_TABLES_SQL)
            for sql in _MIGRATIONS:
                try:
                    cur.execute(sql)
                except Exception as e:
                    logger.warning("Migration skipped: %s | %s", sql[:60], e)
                    conn.rollback()
                    # Re-open cursor after rollback
                    cur.close()
                    cur = conn.cursor()
        conn.commit()
        logger.info("✅ قاعدة البيانات جاهزة (جداول + migrations).")
    except Exception as exc:
        conn.rollback()
        logger.critical("❌ فشل تهيئة قاعدة البيانات: %s", exc)
        raise
    finally:
        pool.putconn(conn)


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(data: dict) -> dict:
    cols   = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    sql    = f"INSERT INTO grid_sessions ({cols}) VALUES ({placeholders}) RETURNING *"
    row    = _exec(sql, tuple(data.values()), fetch="one")
    if not row:
        raise RuntimeError(f"create_session failed — no row returned. data={data!r}")
    return row


def get_session(session_id: str) -> Optional[dict]:
    return _exec(
        "SELECT * FROM grid_sessions WHERE id = %s",
        (session_id,), fetch="one",
    )


def list_active_sessions() -> list[dict]:
    """Returns active + paused sessions (both loaded into engine on startup)."""
    return _exec(
        "SELECT * FROM grid_sessions WHERE status IN ('active','paused') ORDER BY created_at DESC",
        fetch="all",
    ) or []


def list_all_sessions(limit: int = 20) -> list[dict]:
    return _exec(
        "SELECT * FROM grid_sessions ORDER BY created_at DESC LIMIT %s",
        (limit,), fetch="all",
    ) or []


def update_session(session_id: str, data: dict) -> dict:
    if not data:
        return get_session(session_id) or {}
    set_clause = ", ".join(f"{k} = %s" for k in data.keys())
    sql  = f"UPDATE grid_sessions SET {set_clause} WHERE id = %s RETURNING *"
    row  = _exec(sql, (*data.values(), session_id), fetch="one")
    return row or {}


def close_session(session_id: str, pnl: float) -> None:
    update_session(session_id, {
        "status":    "closed",
        "total_pnl": pnl,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Orders ────────────────────────────────────────────────────────────────────

def create_order(data: dict) -> dict:
    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    sql          = f"INSERT INTO grid_orders ({cols}) VALUES ({placeholders}) RETURNING *"
    row          = _exec(sql, tuple(data.values()), fetch="one")
    if not row:
        raise RuntimeError(f"create_order failed — no row returned. data={data!r}")
    return row


def get_session_orders(session_id: str) -> list[dict]:
    return _exec(
        "SELECT * FROM grid_orders WHERE session_id = %s ORDER BY price",
        (session_id,), fetch="all",
    ) or []


def update_order(order_id: str, data: dict) -> None:
    if not data:
        return
    set_clause = ", ".join(f"{k} = %s" for k in data.keys())
    _exec(
        f"UPDATE grid_orders SET {set_clause} WHERE id = %s",
        (*data.values(), order_id),
    )


def cancel_session_orders(session_id: str) -> None:
    _exec(
        "UPDATE grid_orders SET status = 'cancelled' WHERE session_id = %s AND status = 'open'",
        (session_id,),
    )


# ── Trades ────────────────────────────────────────────────────────────────────

def create_trade(data: dict) -> dict:
    cols         = ", ".join(data.keys())
    placeholders = ", ".join(["%s"] * len(data))
    sql          = f"INSERT INTO grid_trades ({cols}) VALUES ({placeholders}) RETURNING *"
    row          = _exec(sql, tuple(data.values()), fetch="one")
    if not row:
        raise RuntimeError(f"create_trade failed — no row returned. data={data!r}")
    return row


def get_session_trades(session_id: str) -> list[dict]:
    return _exec(
        "SELECT * FROM grid_trades WHERE session_id = %s ORDER BY created_at DESC",
        (session_id,), fetch="all",
    ) or []


def session_total_pnl(session_id: str) -> float:
    row = _exec(
        "SELECT COALESCE(SUM(pnl), 0) AS total FROM grid_trades WHERE session_id = %s",
        (session_id,), fetch="one",
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
    row = _exec(
        "SELECT value FROM bot_settings WHERE key = %s",
        (key,), fetch="one",
    )
    return row["value"] if row else None


# ── Templates ─────────────────────────────────────────────────────────────────

def save_template(name: str, cfg: dict) -> None:
    import json
    raw = get_setting("grid_templates")
    templates: list = json.loads(raw) if raw else []
    templates = [t for t in templates if t["name"] != name]
    cfg["name"] = name
    templates.append(cfg)
    set_setting("grid_templates", json.dumps(templates, ensure_ascii=False))


def get_templates() -> list[dict]:
    import json
    raw = get_setting("grid_templates")
    return json.loads(raw) if raw else []


def delete_template(name: str) -> None:
    import json
    templates = [t for t in get_templates() if t["name"] != name]
    set_setting("grid_templates", json.dumps(templates, ensure_ascii=False))
