import os
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from config_manager import PROJECT_ROOT, ConfigManager
from logging_config import get_logger

LOGGER = get_logger(__name__)


MODELS_DIR = PROJECT_ROOT / "models"


def _resolve_configured_path(value):
    resolved = Path(os.path.expandvars(str(value))).expanduser()
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    return resolved


class ModelManager:
    def __init__(self, config=None, models_dir=MODELS_DIR):
        self.config = config or ConfigManager()
        self.models_dir = Path(models_dir)

    def _target_path_from_url(self, url):
        parsed = urlparse(str(url))
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            LOGGER.error("YOLO weights URL must be an HTTPS URL.")
            return None

        filename = Path(parsed.path).name or "rok_yolo_weights.pt"
        if not filename.lower().endswith(".pt"):
            filename = f"{filename}.pt"
        return self.models_dir / filename

    def find_yolo_weights(self):
        configured_path = self.config.get("ROK_YOLO_WEIGHTS")
        if configured_path:
            resolved = _resolve_configured_path(configured_path)
            if resolved.is_file():
                return resolved
            LOGGER.warning(f"Configured YOLO weights are not accessible: {resolved}")

        url = self.config.get("ROK_YOLO_WEIGHTS_URL")
        if not url:
            return None

        final_path = self._target_path_from_url(url)
        if final_path and final_path.is_file():
            self.config.set_many({"ROK_YOLO_WEIGHTS": str(final_path)})
            return final_path
        return None

    def has_configured_download(self):
        return bool(self.config.get("ROK_YOLO_WEIGHTS_URL"))

    def ensure_yolo_weights(self):
        weights_path = self.find_yolo_weights()
        if weights_path:
            return weights_path

        url = self.config.get("ROK_YOLO_WEIGHTS_URL")
        if not url:
            return None

        final_path = self._target_path_from_url(url)
        if not final_path:
            return None

        self.models_dir.mkdir(parents=True, exist_ok=True)
        temp_path = final_path.with_suffix(final_path.suffix + ".tmp")

        if final_path.is_file():
            self.config.set_many({"ROK_YOLO_WEIGHTS": str(final_path)})
            return final_path

        try:
            LOGGER.info(f"Downloading YOLO weights: {url}")
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req) as response, temp_path.open("wb") as out_file:  # nosec B310
                out_file.write(response.read())
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


def yolo_download_required(config=None):
    manager = ModelManager(config)
    return manager.find_yolo_weights() is None and manager.has_configured_download()
