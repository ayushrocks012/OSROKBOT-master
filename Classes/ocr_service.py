from pathlib import Path

from config_manager import ConfigManager
from logging_config import get_logger
from PIL import Image

LOGGER = get_logger(__name__)


class OCRService:
    """Side-by-side OCR service: EasyOCR first, Tesseract fallback."""

    _easyocr_reader = None
    _easyocr_error = None

    def __init__(self, languages=None, config=None):
        self.languages = languages or ["en"]
        self.config = config or ConfigManager()

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
        except Exception as exc:
            cls._easyocr_error = exc
            LOGGER.warning(f"EasyOCR unavailable; using Tesseract fallback when possible: {exc}")
            return None

    @staticmethod
    def _image(input_image):
        if isinstance(input_image, Image.Image):
            return input_image.convert("RGB")
        return Image.open(Path(input_image)).convert("RGB")

    def _read_easyocr(self, image):
        reader = self._reader(self.languages)
        if not reader:
            return ""
        try:
            import numpy as np

            results = reader.readtext(np.asarray(image))
            return " ".join(str(item[1]) for item in results if len(item) >= 2).strip()
        except Exception as exc:
            LOGGER.warning(f"EasyOCR read failed: {exc}")
            return ""

    def _read_tesseract(self, image):
        try:
            import pytesseract

            tesseract_path = self.config.get("TESSERACT_PATH")
            if tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            return pytesseract.image_to_string(image, lang="eng").strip()
        except Exception as exc:
            LOGGER.warning(f"Tesseract fallback failed: {exc}")
            return ""

    def read(self, image_or_roi, purpose=None):
        LOGGER.debug("OCR read purpose: %s", purpose or "general")
        image = self._image(image_or_roi)
        text = self._read_easyocr(image)
        if text:
            return text
        return self._read_tesseract(image)
