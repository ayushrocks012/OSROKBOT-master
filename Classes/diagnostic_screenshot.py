from __future__ import annotations

from datetime import datetime
from pathlib import Path

from logging_config import get_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIAGNOSTICS_DIR = PROJECT_ROOT / "diagnostics"
LOGGER = get_logger(__name__)


def save_diagnostic_screenshot(screenshot, label: str = "diagnostic", diagnostics_dir: Path = DEFAULT_DIAGNOSTICS_DIR) -> Path | None:
    """Persist a screenshot for debugging without invoking legacy image matching."""
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in label).strip("_")
    path = diagnostics_dir / f"{safe_label or 'diagnostic'}_{timestamp}.png"
    try:
        if hasattr(screenshot, "save"):
            screenshot.save(path)
        else:
            return None
    except Exception as exc:
        LOGGER.error("Unable to save diagnostic screenshot: %s", exc)
        return None

    LOGGER.info("Diagnostic screenshot saved: %s", path)
    return path
