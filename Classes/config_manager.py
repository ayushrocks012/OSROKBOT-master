import json
import os
from pathlib import Path

from termcolor import colored


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"


class ConfigManager:
    SUPPORTED_KEYS = {
        "OPENAI_KEY",
        "OPENAI_API_KEY",
        "EMAIL",
        "EMAIL_TO",
        "EMAIL_FROM",
        "EMAIL_PASSWORD",
        "EMAIL_SMTP_SERVER",
        "EMAIL_SMTP_PORT",
        "TESSERACT_PATH",
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
                print(colored(f"Unable to read config.json: {exc}", "yellow"))
        return self

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        allowed_values = {
            key: value
            for key, value in self.values.items()
            if key in self.SUPPORTED_KEYS and value not in {None, ""}
        }
        self.config_path.write_text(json.dumps(allowed_values, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
            if cleaned_value:
                self.values[key] = cleaned_value
            else:
                self.values.pop(key, None)
        self.save()
        return self
