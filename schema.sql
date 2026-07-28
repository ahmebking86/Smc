-- =============================================================
-- Grid Trading Bot — PostgreSQL Schema (Railway)
-- يُشغَّل تلقائياً عند بدء البوت عبر init_db()
-- يمكن تشغيله يدوياً في Railway → Data → Query
-- =============================================================

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
    status            TEXT        NOT NULL DEFAULT 'active',   -- active | paused | closed
    total_pnl         NUMERIC     NOT NULL DEFAULT 0,
    depth             INTEGER     NOT NULL DEFAULT 0,
    parent_id         TEXT        REFERENCES grid_sessions(id),
    trailing_stop     BOOLEAN     NOT NULL DEFAULT FALSE,
    trailing_pct      NUMERIC     NOT NULL DEFAULT 0,
    group_id          TEXT        DEFAULT NULL,
    -- NOTE: auto_shift / shift_count / original_amount were planned but never
    -- implemented. They are NOT in the live DDL (database/db.py) and must NOT
    -- be added here until the feature is built. Kept as a comment for reference.
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
    side        TEXT        NOT NULL,   -- buy | sell
    price       NUMERIC     NOT NULL,
    qty         NUMERIC     NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'open',   -- open | filled | cancelled
    entry_price NUMERIC,               -- سعر الشراء الذي أطلق أمر البيع
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_status ON grid_sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_group  ON grid_sessions(group_id);
CREATE INDEX IF NOT EXISTS idx_orders_session  ON grid_orders(session_id);
CREATE INDEX IF NOT EXISTS idx_orders_status   ON grid_orders(status);
CREATE INDEX IF NOT EXISTS idx_trades_session  ON grid_trades(session_id);
