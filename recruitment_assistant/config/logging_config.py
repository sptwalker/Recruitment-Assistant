import logging
import sys
from pathlib import Path

from loguru import logger


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(level: str = "INFO") -> None:
    Path("logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logger.remove()
    logger.add(sys.stderr, level=level)
    logger.add("logs/app.log", rotation="10 MB", retention="14 days", level=level, encoding="utf-8")
