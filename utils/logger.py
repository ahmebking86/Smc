from loguru import logger
import sys


def setup_logger(level: str = "INFO"):
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=level,
        colorize=True,
    )
    logger.add(
        "logs/judge.log",
        rotation="10 MB",
        retention="7 days",
        level=level,
    )
