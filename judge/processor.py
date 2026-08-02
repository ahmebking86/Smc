"""
Judge - Signal Processor
Reads new signals and makes decisions
"""

from loguru import logger
from telegram.ext import Application
from database.models import get_session, ClusterSignal, SignalStatus, is_paused
from datetime import datetime
import config.settings as config


async def process_new_signals(app: Application = None):
    if is_paused():
        logger.info("System paused – skipping judge cycle")
        return

    logger.info("Processing new signals...")

    session = get_session()
    try:
        new_signals = (
            session.query(ClusterSignal)
            .filter(ClusterSignal.status == SignalStatus.NEW.value)
            .order_by(ClusterSignal.created_at.asc())
            .limit(10)
            .all()
        )

        if not new_signals:
            logger.info("No new signals")
            return

        for signal in new_signals:
            logger.info(
                f"Signal #{signal.id} | {signal.signal_type} | "
                f"{signal.token_symbol} | wallets={signal.wallet_count} | "
                f"score={signal.conviction_score}"
            )

            # Simple decision logic (to be expanded)
            if signal.conviction_score >= config.MIN_CONVICTION_SCORE:
                if signal.signal_type == "cluster_accumulation":
                    logger.success(f"High conviction ACCUMULATION → consider entry")
                    # TODO: call executor
                elif signal.signal_type == "cluster_exit":
                    logger.warning(f"High conviction EXIT → consider closing positions")
                    # TODO: call exit logic
            else:
                logger.info(f"Score too low ({signal.conviction_score}) → ignore")

            signal.status = SignalStatus.PROCESSED.value
            signal.processed_at = datetime.utcnow()
            session.commit()

    except Exception as e:
        logger.exception(f"Judge processing error: {e}")
        session.rollback()
    finally:
        session.close()
