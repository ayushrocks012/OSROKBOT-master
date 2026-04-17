from pathlib import Path

import model_manager
import object_detector
from model_manager import ModelManager, yolo_download_required


class FakeConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.saved = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set_many(self, values):
        self.saved.append(dict(values))
        self.values.update({key: str(value) for key, value in values.items()})
        return self


def test_yolo_download_not_required_without_weights_or_url():
    assert yolo_download_required(FakeConfig()) is False


def test_yolo_download_not_required_when_local_weights_exist():
    weights = Path(__file__)

    assert yolo_download_required(FakeConfig({"ROK_YOLO_WEIGHTS": str(weights)})) is False


def test_yolo_download_required_when_url_configured_without_local_weights():
    config = FakeConfig({"ROK_YOLO_WEIGHTS_URL": "https://example.test/unique-missing-yolo.pt"})

    assert yolo_download_required(config) is True


def test_yolo_download_failure_returns_none_without_configuring_weights(monkeypatch):
    config = FakeConfig({"ROK_YOLO_WEIGHTS_URL": "https://example.test/rok-ui.pt"})
    manager = ModelManager(config=config, models_dir=Path.cwd())

    def fail_urlopen(_request, timeout=None):
        raise OSError("offline")

    monkeypatch.setattr(model_manager, "urlopen", fail_urlopen)

    assert manager.ensure_yolo_weights() is None
    assert "ROK_YOLO_WEIGHTS" not in config.values


def test_yolo_download_rejects_oversized_content_length(monkeypatch, tmp_path):
    config = FakeConfig(
        {
            "ROK_YOLO_WEIGHTS_URL": "https://example.test/rok-ui.pt",
            "ROK_YOLO_MAX_BYTES": "10",
        }
    )
    manager = ModelManager(config=config, models_dir=tmp_path)

    class FakeResponse:
        headers = {"Content-Length": "11"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return b""

    monkeypatch.setattr(model_manager, "urlopen", lambda _request, timeout=None: FakeResponse())

    assert manager.ensure_yolo_weights() is None
    assert not (tmp_path / "rok-ui.pt").exists()
    assert "ROK_YOLO_WEIGHTS" not in config.values


def test_yolo_download_streams_with_timeout(monkeypatch, tmp_path):
    config = FakeConfig({"ROK_YOLO_WEIGHTS_URL": "https://example.test/rok-ui.pt"})
    manager = ModelManager(config=config, models_dir=tmp_path)
    timeouts = []

    class FakeResponse:
        headers = {}

        def __init__(self):
            self.chunks = [b"abc", b"def", b""]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size=-1):
            return self.chunks.pop(0)

    def fake_urlopen(_request, timeout=None):
        timeouts.append(timeout)
        return FakeResponse()

    monkeypatch.setattr(model_manager, "urlopen", fake_urlopen)

    assert manager.ensure_yolo_weights() == tmp_path / "rok-ui.pt"
    assert (tmp_path / "rok-ui.pt").read_bytes() == b"abcdef"
    assert timeouts == [model_manager.DOWNLOAD_TIMEOUT_SECONDS]


def test_create_detector_does_not_download_weights(monkeypatch):
    class FakeManager:
        def find_yolo_weights(self):
            return None

        def ensure_yolo_weights(self):
            raise AssertionError("detector construction must not download weights")

    monkeypatch.setattr(object_detector, "ModelManager", lambda: FakeManager())

    assert isinstance(object_detector.create_detector(), object_detector.NoOpDetector)
