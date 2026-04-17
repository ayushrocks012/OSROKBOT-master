"""Security helpers for local configuration and logging."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from pathlib import Path

SENSITIVE_CONFIG_KEYS = {
    "OPENAI_KEY",
    "OPENAI_API_KEY",
    "EMAIL_PASSWORD",
}

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(OPENAI(?:_API)?_KEY\s*=\s*)([^\s]+)", re.IGNORECASE),
    re.compile(r"(EMAIL_PASSWORD\s*=\s*)([^\s]+)", re.IGNORECASE),
)


def redact_secret(value: object) -> str:
    """Return text with common local secret values redacted."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: f"{match.group(1)}<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    return text


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Atomically write UTF-8 text beside the final file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    if mode is not None:
        with suppress(OSError):
            temp_path.chmod(mode)
    temp_path.replace(path)
    if mode is not None:
        with suppress(OSError):
            path.chmod(mode)


def format_env_value(value: object) -> str:
    """Format a value for a simple dotenv file."""
    text = "" if value is None else str(value)
    if not text or any(char.isspace() for char in text) or any(char in text for char in ['"', "'", "#"]):
        return json.dumps(text)
    return text


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Apply key/value updates to a dotenv file while preserving unrelated lines."""
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(updates)
    output: list[str] = []

    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(raw_line)
            continue

        key, _value = stripped.split("=", 1)
        key = key.strip()
        if key not in remaining:
            output.append(raw_line)
            continue

        value = remaining.pop(key)
        if value:
            output.append(f"{key}={format_env_value(value)}")

    for key in sorted(remaining):
        value = remaining[key]
        if value:
            output.append(f"{key}={format_env_value(value)}")

    atomic_write_text(path, "\n".join(output).rstrip() + "\n", mode=0o600)
