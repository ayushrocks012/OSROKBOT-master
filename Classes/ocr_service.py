"""OCR boundary for planner text/target extraction and fallback reads.

The service keeps OCR side effects inside a single module. It can use EasyOCR
when selected, or bounded Tesseract calls when a local Tesseract executable is
configured. Planner-sized screenshots are downscaled before Tesseract so a
broken or slow OCR path cannot stall the guarded run for minutes.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

from config_manager import ConfigManager
from logging_config import get_logger
from PIL import Image

LOGGER = get_logger(__name__)

OCR_READ_EXCEPTIONS = (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError)
DEFAULT_TESSERACT_TIMEOUT_SECONDS = 5.0
DEFAULT_OCR_MAX_IMAGE_SIDE = 1280


@dataclass(frozen=True)
class OCRRegion:
    """Normalized OCR region returned by EasyOCR or Tesseract helpers."""

    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float

    def to_dict(self):
        """Return this OCR region as a plain JSON-serializable dictionary."""

        return asdict(self)


class OCRService:
    """Side-by-side OCR service with bounded Tesseract fallback."""

    _easyocr_reader = None
    _easyocr_error = None

    def __init__(self, languages=None, config=None):
        self.languages = languages or ["en"]
        self.config = config or ConfigManager()

    @staticmethod
    def _config_float(config, key, default):
        try:
            return float(config.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _config_int(config, key, default):
        try:
            return int(config.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def _engine_order(self):
        engine = str(self.config.get("OCR_ENGINE", "") or "").strip().lower()
        if engine == "easyocr":
            return ("easyocr", "tesseract")
        if engine == "tesseract":
            return ("tesseract",)

        # An explicit Tesseract path usually means the workstation has a known
        # OCR fallback. Prefer it to avoid expensive EasyOCR/Torch startup
        # failures on live automation runs.
        if self.config.get("TESSERACT_PATH"):
            return ("tesseract",)
        return ("easyocr", "tesseract")

    def _tesseract_timeout(self):
        return max(
            1.0,
            self._config_float(self.config, "TESSERACT_TIMEOUT_SECONDS", DEFAULT_TESSERACT_TIMEOUT_SECONDS),
        )

    def _prepare_tesseract_image(self, image):
        max_side = max(0, self._config_int(self.config, "OCR_MAX_IMAGE_SIDE", DEFAULT_OCR_MAX_IMAGE_SIDE))
        if not max_side:
            return image
        width, height = image.size
        longest_side = max(width, height)
        if longest_side <= max_side:
            return image
        scale = max_side / float(longest_side)
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        return image.resize((max(1, round(width * scale)), max(1, round(height * scale))), resampling)

    @classmethod
    def _reader(cls, languages):
        if cls._easyocr_reader is not None:
            return cls._easyocr_reader
        if cls._easyocr_error is not None:
            return None
        try:
            import easyocr

            cls._easyocr_reader = easyocr.Reader(languages, gpu=False, verbose=False)
            return cls._easyocr_reader
        except OCR_READ_EXCEPTIONS as exc:
            cls._easyocr_error = exc
            LOGGER.warning(f"EasyOCR unavailable; using Tesseract fallback when possible: {exc}")
            return None

    @staticmethod
    def _image(input_image):
        if isinstance(input_image, Image.Image):
            return input_image.convert("RGB")
        return Image.open(Path(input_image)).convert("RGB")

    @staticmethod
    def _clamp(value, minimum=0.0, maximum=1.0):
        return max(minimum, min(maximum, float(value)))

    @classmethod
    def _region_from_box(cls, text, confidence, box, image_width, image_height):
        text = str(text or "").strip()
        if not text:
            return None

        try:
            points = [(float(point[0]), float(point[1])) for point in box]
        except (IndexError, TypeError, ValueError):
            return None
        if not points:
            return None

        x_values = [point[0] for point in points]
        y_values = [point[1] for point in points]
        left = max(0.0, min(x_values))
        top = max(0.0, min(y_values))
        right = min(float(image_width), max(x_values))
        bottom = min(float(image_height), max(y_values))
        width = max(0.0, right - left)
        height = max(0.0, bottom - top)
        if width <= 0 or height <= 0:
            return None

        return OCRRegion(
            text=text,
            x=cls._clamp((left + width / 2.0) / max(1, image_width)),
            y=cls._clamp((top + height / 2.0) / max(1, image_height)),
            width=cls._clamp(width / max(1, image_width)),
            height=cls._clamp(height / max(1, image_height)),
            confidence=cls._clamp(confidence),
        )

    @classmethod
    def _region_from_rect(cls, text, confidence, left, top, width, height, image_width, image_height):
        box = [
            (left, top),
            (left + width, top),
            (left + width, top + height),
            (left, top + height),
        ]
        return cls._region_from_box(text, confidence, box, image_width, image_height)

    def _read_easyocr(self, image):
        reader = self._reader(self.languages)
        if not reader:
            return ""
        try:
            import numpy as np

            results = reader.readtext(np.asarray(image))
            return " ".join(str(item[1]) for item in results if len(item) >= 2).strip()
        except OCR_READ_EXCEPTIONS as exc:
            LOGGER.warning(f"EasyOCR read failed: {exc}")
            return ""

    def _read_easyocr_regions(self, image):
        reader = self._reader(self.languages)
        if not reader:
            return []
        try:
            import numpy as np

            image_width, image_height = image.size
            regions = []
            for item in reader.readtext(np.asarray(image)):
                if len(item) < 2:
                    continue
                box = item[0]
                text = item[1]
                confidence = float(item[2]) if len(item) >= 3 else 1.0
                region = self._region_from_box(text, confidence, box, image_width, image_height)
                if region:
                    regions.append(region)
            return regions
        except OCR_READ_EXCEPTIONS as exc:
            LOGGER.warning(f"EasyOCR region read failed: {exc}")
            return []

    def _read_tesseract(self, image):
        try:
            import pytesseract

            tesseract_path = self.config.get("TESSERACT_PATH")
            if tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            image = self._prepare_tesseract_image(image)
            return pytesseract.image_to_string(
                image,
                lang="eng",
                config="--oem 3 --psm 11",
                timeout=self._tesseract_timeout(),
            ).strip()
        except OCR_READ_EXCEPTIONS as exc:
            LOGGER.warning(f"Tesseract fallback failed: {exc}")
            return ""

    def _read_tesseract_regions(self, image):
        try:
            import pytesseract

            tesseract_path = self.config.get("TESSERACT_PATH")
            if tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            image = self._prepare_tesseract_image(image)
            data = pytesseract.image_to_data(
                image,
                lang="eng",
                config="--oem 3 --psm 11",
                output_type=pytesseract.Output.DICT,
                timeout=self._tesseract_timeout(),
            )
        except OCR_READ_EXCEPTIONS as exc:
            LOGGER.warning(f"Tesseract region fallback failed: {exc}")
            return []

        image_width, image_height = image.size
        regions = []
        for index, text in enumerate(data.get("text", [])):
            cleaned_text = str(text or "").strip()
            if not cleaned_text:
                continue
            try:
                confidence = float(data.get("conf", [])[index]) / 100.0
            except (IndexError, TypeError, ValueError):
                confidence = 0.0
            if confidence < 0:
                continue
            try:
                left = float(data.get("left", [])[index])
                top = float(data.get("top", [])[index])
                width = float(data.get("width", [])[index])
                height = float(data.get("height", [])[index])
            except (IndexError, TypeError, ValueError):
                continue
            region = self._region_from_rect(
                cleaned_text,
                confidence,
                left,
                top,
                width,
                height,
                image_width,
                image_height,
            )
            if region:
                regions.append(region)
        return regions

    def read(self, image_or_roi, purpose=None):
        """Read OCR text from one image or ROI using the configured engine order."""

        LOGGER.debug("OCR read purpose: %s", purpose or "general")
        image = self._image(image_or_roi)
        for engine in self._engine_order():
            text = self._read_easyocr(image) if engine == "easyocr" else self._read_tesseract(image)
            if text:
                return text
        return ""

    def read_regions(self, image_or_roi, purpose=None):
        """Read OCR regions from one image or ROI using the configured engine order."""

        LOGGER.debug("OCR region read purpose: %s", purpose or "general")
        image = self._image(image_or_roi)
        for engine in self._engine_order():
            regions = self._read_easyocr_regions(image) if engine == "easyocr" else self._read_tesseract_regions(image)
            if regions:
                return regions
        return []
