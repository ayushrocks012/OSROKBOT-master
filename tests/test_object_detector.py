import sys
from pathlib import Path
from types import SimpleNamespace

import object_detector
import pytest
from object_detector import NoOpDetector, YOLODetector, create_detector
from PIL import Image


class _ArrayLike:
    def __init__(self, values):
        self._values = values

    def __getitem__(self, index):
        return _ArrayLike(self._values[index]) if isinstance(self._values[index], list) else self._values[index]

    def tolist(self):
        return self._values


class _FakeBox:
    xyxy = _ArrayLike([[10.0, 20.0, 50.0, 80.0]])
    cls = [1]
    conf = [0.87]


@pytest.mark.integration
def test_yolo_detector_normalizes_model_boxes(monkeypatch, tmp_path):
    calls = []

    class FakeYOLO:
        def __init__(self, weights_path):
            calls.append(Path(weights_path).name)

        def __call__(self, image, verbose=False):
            assert image.size == (100, 200)
            assert verbose is False
            return [SimpleNamespace(names={1: "Gather Button"}, boxes=[_FakeBox()])]

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))
    weights = tmp_path / "weights.pt"
    weights.write_text("fake", encoding="utf-8")

    detector = YOLODetector(weights)
    detections = detector.detect(Image.new("RGB", (100, 200)))

    assert calls == ["weights.pt"]
    assert len(detections) == 1
    assert detections[0].label == "Gather Button"
    assert detections[0].x == pytest.approx(0.30)
    assert detections[0].y == pytest.approx(0.25)
    assert detections[0].width == pytest.approx(0.40)
    assert detections[0].height == pytest.approx(0.30)
    assert detections[0].confidence == pytest.approx(0.87)
    assert detections[0].to_dict()["label"] == "Gather Button"


def test_noop_detector_returns_empty_detections():
    assert NoOpDetector().detect(object()) == []


def test_create_detector_returns_noop_when_weights_missing(monkeypatch):
    monkeypatch.setattr(object_detector.ModelManager, "find_yolo_weights", lambda self: "")

    assert isinstance(create_detector(), NoOpDetector)


def test_create_detector_returns_noop_when_yolo_load_fails(monkeypatch, tmp_path):
    weights = tmp_path / "weights.pt"
    weights.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(object_detector.ModelManager, "find_yolo_weights", lambda self: weights)
    monkeypatch.setattr(
        object_detector,
        "YOLODetector",
        lambda _weights: (_ for _ in ()).throw(RuntimeError("bad weights")),
    )

    assert isinstance(create_detector(), NoOpDetector)
