import json
import os
from pathlib import Path

from logging_config import get_logger
from security_utils import SENSITIVE_CONFIG_KEYS, atomic_write_text, update_env_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"
LOGGER = get_logger(__name__)


class ConfigManager:
    SENSITIVE_KEYS = SENSITIVE_CONFIG_KEYS
    SUPPORTED_KEYS = {
        "OPENAI_KEY",
        "OPENAI_API_KEY",
        "EMAIL",
        "EMAIL_TO",
        "EMAIL_FROM",
        "EMAIL_PASSWORD",
        "EMAIL_SMTP_SERVER",
        "EMAIL_SMTP_PORT",
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
    }

    def __init__(self, config_path=CONFIG_PATH, env_path=ENV_PATH):
        self.config_path = Path(config_path)
        self.env_path = Path(env_path)
        self.values = {}
        self.env_values = {}
        self.load()

    @staticmethod
    def _read_env_file(path):
        values = {}
        if not path.is_file():
            return values
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
        return values

    def load(self):
        self.env_values = self._read_env_file(self.env_path)
        self.values = {}
        if self.config_path.is_file():
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.values = {str(key): str(value) for key, value in raw.items() if value is not None}
            except Exception as exc:
                LOGGER.warning("Unable to read config.json: %s", exc)
        for key in self.SENSITIVE_KEYS:
            if key in self.values and key not in self.env_values:
                self.env_values[key] = self.values[key]
            self.values.pop(key, None)
        return self

    def save(self):
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
        atomic_write_text(
            self.config_path,
            json.dumps(public_values, indent=2, sort_keys=True) + "\n",
        )
        secret_values = {
            key: value
            for key, value in self.env_values.items()
            if key in self.SENSITIVE_KEYS
        }
        if secret_values or self.env_path.is_file():
            update_env_file(self.env_path, secret_values)
        return self.config_path

    def get(self, key, default=None):
        if key in self.values and self.values[key] != "":
            return self.values[key]
        if key in self.env_values and self.env_values[key] != "":
            return self.env_values[key]
        return os.getenv(key, default)

    def set_many(self, values):
        for key, value in values.items():
            if key not in self.SUPPORTED_KEYS:
                continue
            cleaned_value = "" if value is None else str(value).strip()
            if key in self.SENSITIVE_KEYS:
                self.values.pop(key, None)
                if cleaned_value:
                    self.env_values[key] = cleaned_value
                else:
                    self.env_values.pop(key, None)
                continue
            if cleaned_value:
                self.values[key] = cleaned_value
            else:
                self.values.pop(key, None)
        self.save()
        return self
