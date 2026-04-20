"""Configuration manager for public settings and pluggable secret storage.

This module owns the boundary between public runtime configuration persisted in
`config.json` and sensitive configuration resolved through a secret-provider
chain. The default secret backend remains the project `.env`, while Windows
operators can switch to a DPAPI-backed local encrypted store without changing
runtime callers.

Side Effects:
    Reads and writes `config.json`, `.env`, and the active secret backend.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from logging_config import get_logger
from secret_providers import (
    DEFAULT_DPAPI_STORE_PATH,
    ChainSecretProvider,
    DotenvSecretProvider,
    DpapiSecretProvider,
    EnvironmentSecretProvider,
    SecretProvider,
)
from security_utils import SENSITIVE_CONFIG_KEYS, atomic_write_text, parse_env_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"
LOGGER = get_logger(__name__)


class ConfigManager:
    """Resolve OSROKBOT configuration and persist supported updates.

    Sensitive keys are never written to `config.json`. They are read from and
    written to the active secret provider, which defaults to `.env` and can be
    switched to Windows DPAPI through `SECRET_PROVIDER=dpapi`.
    """

    SENSITIVE_KEYS = SENSITIVE_CONFIG_KEYS
    SUPPORTED_KEYS = {
        "OPENAI_KEY",
        "OPENAI_API_KEY",
        "RUNTIME_JOURNAL_HMAC_KEY",
        "OCR_ENGINE",
        "OCR_MAX_IMAGE_SIDE",
        "TESSERACT_PATH",
        "TESSERACT_TIMEOUT_SECONDS",
        "ROK_YOLO_WEIGHTS",
        "ROK_YOLO_WEIGHTS_URL",
        "OPENAI_VISION_MODEL",
        "ANTIALIAS_METHOD",
        "ROK_CLIENT_PATH",
        "ROK_WINDOW_TITLE",
        "WINDOW_TITLE",
        "PLANNER_GOAL",
        "PLANNER_AUTONOMY_LEVEL",
        "PLANNER_L1_REVIEW_MIN_CONFIDENCE",
        "PLANNER_TRUSTED_SUCCESS_COUNT",
        "WATCHDOG_HEARTBEAT_PATH",
        "WATCHDOG_TIMEOUT_SECONDS",
        "WATCHDOG_GAME_RESTART_WAIT_SECONDS",
        "WATCHDOG_RESTART_ENABLED",
        "PLANNER_CACHE_TTL_SECONDS",
        "PLANNER_STUCK_THRESHOLD",
        "PLANNER_MAX_MEMORY_ENTRIES",
        "ROK_YOLO_MAX_BYTES",
        "MISSION_HISTORY",
        "SECRET_PROVIDER",
        "DPAPI_SECRET_STORE_PATH",
        "TEACHING_MODE_ENABLED",
        "TEACHING_PROFILE_NAME",
        "TEACHING_NOTES",
    }

    def __init__(
        self,
        config_path: Path = CONFIG_PATH,
        env_path: Path = ENV_PATH,
        secret_provider: SecretProvider | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.env_path = Path(env_path)
        self._secret_provider_override = secret_provider
        self.values: dict[str, str] = {}
        self.env_values: dict[str, str] = {}
        self.secret_values: dict[str, str] = {}
        self._cleared_secret_keys: set[str] = set()
        self.secret_provider_name = "dotenv"
        self.secret_provider: SecretProvider = secret_provider or DotenvSecretProvider(self.env_path)
        self.load()

    def _resolve_unmanaged_value(self, key: str, default: Any = None) -> Any:
        if key in self.values and self.values[key] != "":
            return self.values[key]
        if key in self.env_values and self.env_values[key] != "":
            return self.env_values[key]
        return os.getenv(key, default)

    def _resolve_secret_provider_name(self) -> str:
        configured = (
            os.getenv("OSROKBOT_SECRET_PROVIDER")
            or self._resolve_unmanaged_value("SECRET_PROVIDER", "dotenv")
            or "dotenv"
        )
        return str(configured).strip().lower() or "dotenv"

    def _resolve_dpapi_store_path(self) -> Path:
        configured = (
            os.getenv("OSROKBOT_DPAPI_SECRET_STORE_PATH")
            or self._resolve_unmanaged_value("DPAPI_SECRET_STORE_PATH")
        )
        return Path(configured) if configured else DEFAULT_DPAPI_STORE_PATH

    def _build_secret_provider(self) -> SecretProvider:
        dotenv_provider = DotenvSecretProvider(self.env_path)
        environment_provider = EnvironmentSecretProvider()
        provider_name = self._resolve_secret_provider_name()

        if provider_name == "dpapi":
            try:
                provider = ChainSecretProvider(
                    primary=DpapiSecretProvider(self._resolve_dpapi_store_path()),
                    fallbacks=(dotenv_provider, environment_provider),
                    cleanup_on_write=(dotenv_provider,),
                )
                self.secret_provider_name = provider.active_name
                return provider
            except RuntimeError as exc:
                LOGGER.warning("DPAPI secret provider unavailable; falling back to dotenv: %s", exc)

        if provider_name not in {"dotenv", "dpapi"}:
            LOGGER.warning("Unknown secret provider %r; falling back to dotenv.", provider_name)

        provider = ChainSecretProvider(
            primary=dotenv_provider,
            fallbacks=(environment_provider,),
        )
        self.secret_provider_name = provider.active_name
        return provider

    @staticmethod
    def _primary_provider_name(provider: SecretProvider) -> str:
        return str(getattr(provider, "active_name", getattr(provider, "name", "custom")))

    @staticmethod
    def _primary_provider(provider: SecretProvider) -> SecretProvider:
        return getattr(provider, "primary", provider)

    def load(self) -> ConfigManager:
        """Load current public configuration and resolve secret values."""

        self.env_values = parse_env_file(self.env_path)
        self.values = {}
        self.secret_values = {}
        self._cleared_secret_keys = set()

        if self.config_path.is_file():
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.values = {str(key): str(value) for key, value in raw.items() if value is not None}
            except Exception as exc:
                LOGGER.warning("Unable to read config.json: %s", exc)

        legacy_secret_values: dict[str, str] = {}
        for key in self.SENSITIVE_KEYS:
            if key in self.values:
                legacy_secret_values[key] = self.values.pop(key)

        self.secret_provider = self._secret_provider_override or self._build_secret_provider()
        self.secret_provider_name = self._primary_provider_name(self.secret_provider)

        for key in self.SENSITIVE_KEYS:
            resolved = self.secret_provider.get(key)
            if resolved not in {None, ""}:
                self.secret_values[key] = str(resolved)
                continue
            legacy_value = legacy_secret_values.get(key)
            if legacy_value not in {None, ""}:
                self.secret_values[key] = legacy_value

        return self

    def save(self) -> Path:
        """Persist public config and write sensitive keys to the active backend."""

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        allowed_values = {
            key: value
            for key, value in self.values.items()
            if key in self.SUPPORTED_KEYS and value not in {None, ""}
        }
        public_values = {
            key: value
            for key, value in allowed_values.items()
            if key not in self.SENSITIVE_KEYS
        }

        previous_provider = self.secret_provider
        if self._secret_provider_override is None:
            self.secret_provider = self._build_secret_provider()
            self.secret_provider_name = self._primary_provider_name(self.secret_provider)

        atomic_write_text(
            self.config_path,
            json.dumps(public_values, indent=2, sort_keys=True) + "\n",
        )

        secret_updates = {
            key: value
            for key, value in self.secret_values.items()
            if key in self.SENSITIVE_KEYS and value not in {None, ""}
        }
        for key in self._cleared_secret_keys:
            secret_updates[key] = None
        if secret_updates:
            self.secret_provider.set_many(secret_updates)

        previous_primary = self._primary_provider(previous_provider)
        current_primary = self._primary_provider(self.secret_provider)
        if (
            secret_updates
            and previous_primary is not current_primary
            and self._primary_provider_name(previous_provider) != self.secret_provider_name
        ):
            previous_primary.set_many({key: None for key in self.SENSITIVE_KEYS})

        self._cleared_secret_keys.clear()
        return self.config_path

    def get(self, key: str, default: Any = None) -> Any:
        """Return a public or secret configuration value."""

        if key in self.values and self.values[key] != "":
            return self.values[key]
        if key in self.SENSITIVE_KEYS:
            if key in self.secret_values and self.secret_values[key] != "":
                return self.secret_values[key]
            resolved = self.secret_provider.get(key)
            if resolved not in {None, ""}:
                self.secret_values[key] = str(resolved)
                return resolved
            return os.getenv(key, default)
        if key in self.env_values and self.env_values[key] != "":
            return self.env_values[key]
        return os.getenv(key, default)

    def set_many(self, values: dict[str, Any]) -> ConfigManager:
        """Persist multiple public or secret configuration values."""

        for key, value in values.items():
            if key not in self.SUPPORTED_KEYS:
                continue
            cleaned_value = "" if value is None else str(value).strip()
            if key in self.SENSITIVE_KEYS:
                self.values.pop(key, None)
                if cleaned_value:
                    self.secret_values[key] = cleaned_value
                    self._cleared_secret_keys.discard(key)
                else:
                    self.secret_values.pop(key, None)
                    self._cleared_secret_keys.add(key)
                continue
            if cleaned_value:
                self.values[key] = cleaned_value
            else:
                self.values.pop(key, None)
        self.save()
        return self
