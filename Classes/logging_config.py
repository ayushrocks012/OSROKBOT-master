from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from security_utils import redact_secret
from termcolor import colored

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "logs" / "osrokbot.log"
LOGGER_NAME = "osrokbot"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_CONSOLE_LOG_LEVEL = "ERROR"
DEFAULT_CONSOLE_LOG_FORMAT = "plain"
DEFAULT_FILE_LOG_FORMAT = "json"
LOG_CONTEXT_FIELDS = (
    "run_id",
    "session_id",
    "run_kind",
    "machine_id",
    "step_id",
    "decision_id",
)
_LOG_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("osrokbot_log_context", default=None)
_RESERVED_LOG_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def _local_env_value(key: str) -> str | None:
    if os.getenv(key):
        return os.getenv(key)
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return None
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, value = line.split("=", 1)
            if env_key.strip() == key:
                return value.strip().strip("\"'")
    except OSError:
        return None
    return None


def _level_from_name(value: str | None, default: int) -> int:
    level_name = str(value or "").strip().upper()
    if not level_name:
        return default
    return int(getattr(logging, level_name, default))


def _format_name(*keys: str, default: str) -> str:
    for key in keys:
        value = str(_local_env_value(key) or "").strip().lower()
        if value:
            return "json" if value == "json" else "plain"
    return default


def _utc_timestamp(created_at: float) -> str:
    timestamp = datetime.fromtimestamp(created_at, tz=UTC)
    return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_safe(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secret(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    return redact_secret(str(value))


def current_log_context() -> dict[str, Any]:
    """Return the current structured logging context for this execution context."""

    return dict(_LOG_CONTEXT.get() or {})


def bind_log_context(**fields: Any) -> Token[dict[str, Any] | None]:
    """Merge structured logging fields into the current execution context."""

    merged = current_log_context()
    for key, value in fields.items():
        if key not in LOG_CONTEXT_FIELDS:
            continue
        if value is None or value == "":
            merged.pop(key, None)
        else:
            merged[key] = value
    return _LOG_CONTEXT.set(merged)


def reset_log_context(token: Token[dict[str, Any] | None]) -> None:
    """Restore a previous structured logging context snapshot."""

    _LOG_CONTEXT.reset(token)


@contextmanager
def scoped_log_context(**fields: Any) -> Iterator[None]:
    """Temporarily bind structured logging fields for one execution scope."""

    token = bind_log_context(**fields)
    try:
        yield
    finally:
        reset_log_context(token)


class ColoredFormatter(logging.Formatter):
    """Render console log lines with severity-based terminal colors."""

    COLORS = {
        logging.DEBUG: "cyan",
        logging.INFO: "green",
        logging.WARNING: "yellow",
        logging.ERROR: "red",
        logging.CRITICAL: "red",
    }

    def format(self, record: logging.LogRecord) -> str:
        """Return one colored console log line for the supplied record."""

        message = super().format(record)
        color = self.COLORS.get(record.levelno)
        return colored(message, color) if color else message


class JsonFormatter(logging.Formatter):
    """Render one structured log entry per line for external ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        """Return one JSON log line with redacted structured fields."""

        message = redact_secret(record.getMessage())
        payload: dict[str, Any] = {
            "timestamp": _utc_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "thread_name": record.threadName,
            "process_id": record.process,
        }
        for field_name in LOG_CONTEXT_FIELDS:
            field_value = getattr(record, field_name, None)
            if field_value not in {None, ""}:
                payload[field_name] = _json_safe(field_value)

        extra_fields: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS or key in LOG_CONTEXT_FIELDS or key.startswith("_"):
                continue
            extra_fields[key] = _json_safe(value)
        if extra_fields:
            payload["fields"] = extra_fields

        if record.exc_info:
            payload["exception"] = redact_secret(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = redact_secret(self.formatStack(record.stack_info))
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class RedactingFilter(logging.Filter):
    """Remove local secrets and inject structured log context into records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the log record message and attach bound context fields."""

        rendered = record.getMessage()
        redacted = redact_secret(rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        for field_name, field_value in current_log_context().items():
            if field_name not in LOG_CONTEXT_FIELDS:
                continue
            if getattr(record, field_name, None) in {None, ""}:
                setattr(record, field_name, field_value)
        return True


def configure_logging(log_path: Path | str = DEFAULT_LOG_PATH) -> logging.Logger:
    """Configure and return the shared OSROKBOT root logger."""

    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(_level_from_name(_local_env_value("OSROKBOT_LOG_LEVEL"), getattr(logging, DEFAULT_LOG_LEVEL)))
    logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setLevel(
        _level_from_name(_local_env_value("OSROKBOT_CONSOLE_LOG_LEVEL"), getattr(logging, DEFAULT_CONSOLE_LOG_LEVEL))
    )
    console_handler.addFilter(RedactingFilter())
    if _format_name("OSROKBOT_CONSOLE_LOG_FORMAT", "OSROKBOT_LOG_FORMAT", default=DEFAULT_CONSOLE_LOG_FORMAT) == "json":
        console_handler.setFormatter(JsonFormatter())
    else:
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
    file_handler.setLevel(_level_from_name(_local_env_value("OSROKBOT_FILE_LOG_LEVEL"), logging.INFO))
    file_handler.addFilter(RedactingFilter())
    if _format_name("OSROKBOT_FILE_LOG_FORMAT", "OSROKBOT_LOG_FORMAT", default=DEFAULT_FILE_LOG_FORMAT) == "json":
        file_handler.setFormatter(JsonFormatter())
    else:
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(threadName)s: %(message)s")
        )
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the shared root logger or one of its children."""

    logger = configure_logging()
    if not name:
        return logger
    return logger.getChild(name)
