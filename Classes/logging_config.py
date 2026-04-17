from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from security_utils import redact_secret
from termcolor import colored

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "logs" / "osrokbot.log"
LOGGER_NAME = "osrokbot"


class ColoredFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "cyan",
        logging.INFO: "green",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "red",
    }

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        color = self.COLORS.get(record.levelno)
        return colored(message, color) if color else message


class RedactingFilter(logging.Filter):
    """Remove local secret values from all OSROKBOT log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        redacted = redact_secret(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(log_path: Path | str = DEFAULT_LOG_PATH) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.addFilter(RedactingFilter())
    console_handler.setFormatter(ColoredFormatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(console_handler)

    resolved_log_path = Path(log_path)
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        resolved_log_path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(threadName)s: %(message)s")
    )
    file_handler.addFilter(RedactingFilter())
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    logger = configure_logging()
    if not name:
        return logger
    return logger.getChild(name)
