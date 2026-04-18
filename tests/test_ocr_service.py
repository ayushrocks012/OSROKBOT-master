import sys
from types import SimpleNamespace

import pytest
from ocr_service import OCRService
from PIL import Image


class _EasyOCRReader:
    def readtext(self, _image):
        return [
            (
                [(10, 20), (30, 20), (30, 40), (10, 40)],
                "March",
                0.75,
            )
        ]


class _FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_read_regions_returns_normalized_easyocr_boxes(monkeypatch):
    monkeypatch.setattr(OCRService, "_easyocr_reader", _EasyOCRReader())
    monkeypatch.setattr(OCRService, "_easyocr_error", None)
    service = OCRService(config=_FakeConfig())

    regions = service.read_regions(Image.new("RGB", (100, 100)), purpose="planner")

    assert len(regions) == 1
    assert regions[0].text == "March"
    assert regions[0].x == pytest.approx(0.20)
    assert regions[0].y == pytest.approx(0.30)
    assert regions[0].width == pytest.approx(0.20)
    assert regions[0].height == pytest.approx(0.20)
    assert regions[0].confidence == pytest.approx(0.75)


def test_read_regions_falls_back_to_tesseract_boxes(monkeypatch):
    service = OCRService(config=_FakeConfig())
    monkeypatch.setattr(service, "_read_easyocr_regions", lambda _image: [])
    fake_pytesseract = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        pytesseract=SimpleNamespace(tesseract_cmd=""),
        image_to_data=lambda *_args, **_kwargs: {
            "text": ["", "Help"],
            "conf": ["-1", "62"],
            "left": [0, 40],
            "top": [0, 10],
            "width": [0, 20],
            "height": [0, 30],
        },
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)

    regions = service.read_regions(Image.new("RGB", (100, 100)), purpose="planner")

    assert len(regions) == 1
    assert regions[0].text == "Help"
    assert regions[0].x == pytest.approx(0.50)
    assert regions[0].y == pytest.approx(0.25)
    assert regions[0].confidence == pytest.approx(0.62)


def test_configured_tesseract_path_skips_easyocr(monkeypatch):
    service = OCRService(config=_FakeConfig({"TESSERACT_PATH": "C:/Tesseract/tesseract.exe"}))
    monkeypatch.setattr(service, "_read_easyocr", lambda _image: (_ for _ in ()).throw(AssertionError("easyocr")))
    monkeypatch.setattr(service, "_read_tesseract", lambda _image: "Search")

    assert service.read(Image.new("RGB", (50, 50)), purpose="planner") == "Search"


def test_tesseract_region_read_uses_configured_timeout(monkeypatch):
    service = OCRService(config=_FakeConfig({"TESSERACT_TIMEOUT_SECONDS": "2.5"}))
    captured = {}

    def image_to_data(*_args, **kwargs):
        captured.update(kwargs)
        return {"text": [], "conf": [], "left": [], "top": [], "width": [], "height": []}

    fake_pytesseract = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        pytesseract=SimpleNamespace(tesseract_cmd=""),
        image_to_data=image_to_data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)

    assert service._read_tesseract_regions(Image.new("RGB", (100, 100))) == []
    assert captured["timeout"] == pytest.approx(2.5)
    assert "--psm 11" in captured["config"]


@pytest.mark.integration
def test_read_falls_back_to_tesseract_text(monkeypatch):
    service = OCRService(config=_FakeConfig())
    monkeypatch.setattr(service, "_read_easyocr", lambda _image: "")
    fake_pytesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd=""),
        image_to_string=lambda *_args, **_kwargs: "  Search  ",
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)

    assert service.read(Image.new("RGB", (50, 50)), purpose="planner") == "Search"


def test_invalid_easyocr_box_is_ignored(monkeypatch):
    class BadBoxReader:
        def readtext(self, _image):
            return [("not-a-box", "Bad", 0.5)]

    monkeypatch.setattr(OCRService, "_easyocr_reader", BadBoxReader())
    monkeypatch.setattr(OCRService, "_easyocr_error", None)
    service = OCRService(config=_FakeConfig())
    monkeypatch.setattr(service, "_read_tesseract_regions", lambda _image: [])

    assert service.read_regions(Image.new("RGB", (100, 100)), purpose="planner") == []
