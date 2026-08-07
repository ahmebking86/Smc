"""
تسجيل الصفقات والإشارات في قاعدة بيانات Postgres (Railway).
Railway بيوفر متغير DATABASE_URL تلقائياً لما تضيف Postgres plugin للمشروع.
"""
import logging
import psycopg2
import psycopg2.extras

from .config import Config

logger = logging.getLogger("database")


class Database:
    def __init__(self):
        self.dsn = Config.DATABASE_URL
        self._init_schema()

    def _connect(self):
        return psycopg2.connect(self.dsn)

    def _init_schema(self):
        schema = """
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            amount DOUBLE PRECISION NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION,
            take_profit DOUBLE PRECISION,
            exit_price DOUBLE PRECISION,
            pnl_usdt DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'open',
            dry_run BOOLEAN NOT NULL DEFAULT TRUE,
            reason TEXT,
            opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS signals_log (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema)
            conn.commit()

    def log_signal(self, symbol: str, action: str, price: float, reason: str):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO signals_log (symbol, action, price, reason) VALUES (%s,%s,%s,%s)",
                    (symbol, action, price, reason),
                )
            conn.commit()

    def open_trade(self, symbol, side, amount, entry_price, stop_loss, take_profit, reason, dry_run) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO trades (symbol, side, amount, entry_price, stop_loss, take_profit, reason, dry_run)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (symbol, side, amount, entry_price, stop_loss, take_profit, reason, dry_run),
                )
                trade_id = cur.fetchone()[0]
            conn.commit()
        return trade_id

    def close_trade(self, trade_id: int, exit_price: float, pnl_usdt: float):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE trades SET exit_price=%s, pnl_usdt=%s, status='closed', closed_at=now()
                       WHERE id=%s""",
                    (exit_price, pnl_usdt, trade_id),
                )
            conn.commit()

    def open_trades(self):
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM trades WHERE status='open'")
                return cur.fetchall()
