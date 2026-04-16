from dataclasses import asdict, dataclass
from pathlib import Path

from logging_config import get_logger
from model_manager import ModelManager
from PIL import Image

LOGGER = get_logger(__name__)


@dataclass
class Detection:
    label: str
    x: float
    y: float
    width: float
    height: float
    confidence: float

    def to_dict(self):
        return asdict(self)


class NoOpDetector:
    def detect(self, screenshot):
        return []


class YOLODetector:
    def __init__(self, weights_path):
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("ultralytics is required when ROK_YOLO_WEIGHTS is configured") from exc

        self.model = YOLO(str(weights_path))

    def detect(self, screenshot):
        image = screenshot
        if isinstance(screenshot, str | Path):
            image = Image.open(screenshot).convert("RGB")

        image_width, image_height = image.size
        results = self.model(image, verbose=False)
        detections = []

        for result in results:
            names = getattr(result, "names", {}) or {}
            for box in getattr(result, "boxes", []):
                xyxy = box.xyxy[0].tolist()
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = xyxy
                center_x = (x1 + x2) / 2.0
                center_y = (y1 + y2) / 2.0
                detections.append(
                    Detection(
                        label=str(names.get(class_id, class_id)),
                        x=max(0.0, min(1.0, center_x / image_width)),
                        y=max(0.0, min(1.0, center_y / image_height)),
                        width=max(0.0, min(1.0, (x2 - x1) / image_width)),
                        height=max(0.0, min(1.0, (y2 - y1) / image_height)),
                        confidence=confidence,
                    )
                )

        return detections


def create_detector():
    weights_path = ModelManager().find_yolo_weights()
    if not weights_path:
        return NoOpDetector()

    resolved = Path(weights_path)
    if not resolved.is_file():
        LOGGER.warning(f"YOLO detector disabled: weights not found: {resolved}")
        return NoOpDetector()

    try:
        return YOLODetector(resolved)
    except Exception as exc:
        LOGGER.warning(f"YOLO detector disabled: {exc}")
        return NoOpDetector()
