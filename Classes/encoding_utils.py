"""Shared encoding and parsing helpers used across planner and fallback modules."""

import base64
import json
from pathlib import Path


def image_data_url(path):
    """Encode an image file as a data URL for OpenAI vision input.

    Args:
        path: Path to a PNG/JPEG screenshot.

    Returns:
        str: A ``data:image/...;base64,...`` URL.
    """
    path = Path(path)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    suffix = path.suffix.lower().lstrip(".") or "png"
    if suffix == "jpg":
        suffix = "jpeg"
    return f"data:image/{suffix};base64,{encoded}"


def safe_json_loads(text):
    """Parse strict JSON, with a small fallback for wrapped model output.

    Args:
        text: Raw model output text.

    Returns:
        dict: Parsed JSON object.
    """
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise
