import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlretrieve

from termcolor import colored

from config_manager import ConfigManager, PROJECT_ROOT


MODELS_DIR = PROJECT_ROOT / "models"


class ModelManager:
    def __init__(self, config=None, models_dir=MODELS_DIR):
        self.config = config or ConfigManager()
        self.models_dir = Path(models_dir)

    def ensure_yolo_weights(self):
        configured_path = self.config.get("ROK_YOLO_WEIGHTS")
        if configured_path:
            resolved = Path(os.path.expandvars(configured_path))
            if not resolved.is_absolute():
                resolved = PROJECT_ROOT / resolved
            if resolved.is_file():
                return resolved
            print(colored(f"Configured YOLO weights are not accessible: {resolved}", "yellow"))

        url = self.config.get("ROK_YOLO_WEIGHTS_URL")
        if not url:
            return None

        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            print(colored("YOLO weights URL must be an HTTPS URL.", "red"))
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
            print(colored(f"Downloading YOLO weights: {url}", "cyan"))
            urlretrieve(url, temp_path)
            temp_path.replace(final_path)
        except Exception as exc:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            print(colored(f"YOLO weights download failed: {exc}", "red"))
            return None

        self.config.set_many({"ROK_YOLO_WEIGHTS": str(final_path)})
        print(colored(f"YOLO weights saved: {final_path}", "green"))
        return final_path
