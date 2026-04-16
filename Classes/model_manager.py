import os
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlretrieve

from config_manager import PROJECT_ROOT, ConfigManager
from logging_config import get_logger

LOGGER = get_logger(__name__)


MODELS_DIR = PROJECT_ROOT / "models"
ENV_VAR_PATTERN = re.compile(r"%([^%]+)%|\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _expand_env_vars(value):
    def replace(match):
        key = next(group for group in match.groups() if group)
        return os.environ.get(key, match.group(0))

    return ENV_VAR_PATTERN.sub(replace, str(value))


class ModelManager:
    def __init__(self, config=None, models_dir=MODELS_DIR):
        self.config = config or ConfigManager()
        self.models_dir = Path(models_dir)

    def ensure_yolo_weights(self):
        configured_path = self.config.get("ROK_YOLO_WEIGHTS")
        if configured_path:
            resolved = Path(_expand_env_vars(configured_path)).expanduser()
            if not resolved.is_absolute():
                resolved = PROJECT_ROOT / resolved
            if resolved.is_file():
                return resolved
            LOGGER.warning(f"Configured YOLO weights are not accessible: {resolved}")

        url = self.config.get("ROK_YOLO_WEIGHTS_URL")
        if not url:
            return None

        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            LOGGER.error("YOLO weights URL must be an HTTPS URL.")
            return None

        filename = Path(parsed.path).name or "rok_yolo_weights.pt"
        if not filename.lower().endswith(".pt"):
            filename = f"{filename}.pt"

        self.models_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.models_dir / filename
        temp_path = final_path.with_suffix(final_path.suffix + ".tmp")

        if final_path.is_file():
            self.config.set_many({"ROK_YOLO_WEIGHTS": str(final_path)})
            return final_path

        try:
            LOGGER.info(f"Downloading YOLO weights: {url}")
            urlretrieve(url, temp_path)
            temp_path.replace(final_path)
        except Exception as exc:
            if temp_path.exists():
                with suppress(OSError):
                    temp_path.unlink()
            LOGGER.error(f"YOLO weights download failed: {exc}")
            return None

        self.config.set_many({"ROK_YOLO_WEIGHTS": str(final_path)})
        LOGGER.info(f"YOLO weights saved: {final_path}")
        return final_path
