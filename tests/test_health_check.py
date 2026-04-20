import sys
from types import ModuleType

import pytest
from health_check import HealthCheckDialog, _YoloDownloadWorker


class _FakeConfig:
    def __init__(self, values):
        self.values = dict(values)
        self.saved_updates = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def load(self):
        return self

    def set_many(self, values):
        self.saved_updates.append(dict(values))
        self.values.update({key: value for key, value in values.items() if value is not None})


def test_health_check_should_show_when_api_key_is_missing():
    assert HealthCheckDialog.should_show(config=_FakeConfig({})) is True


def test_health_check_should_show_when_interception_is_available(monkeypatch):
    fake_interception = ModuleType("interception")
    fake_interception.auto_capture_devices = lambda: None
    monkeypatch.setitem(sys.modules, "interception", fake_interception)

    assert HealthCheckDialog.should_show(config=_FakeConfig({"OPENAI_KEY": "sk-test-secret"})) is False


def test_health_check_run_checks_updates_status_labels(monkeypatch, qapp, tmp_path):
    del qapp
    fake_interception = ModuleType("interception")
    fake_interception.auto_capture_devices = lambda: None
    fake_pygetwindow = ModuleType("pygetwindow")
    fake_pygetwindow.getWindowsWithTitle = lambda _title: [object()]
    monkeypatch.setitem(sys.modules, "interception", fake_interception)
    monkeypatch.setitem(sys.modules, "pygetwindow", fake_pygetwindow)
    monkeypatch.setattr("health_check.ConfigManager", lambda: _FakeConfig({}))
    monkeypatch.setattr("health_check.QtCore.QTimer.singleShot", lambda *_args, **_kwargs: None)

    yolo_weights = tmp_path / "weights.pt"
    tesseract_path = tmp_path / "tesseract.exe"
    yolo_weights.write_text("weights", encoding="utf-8")
    tesseract_path.write_text("exe", encoding="utf-8")

    dialog = HealthCheckDialog()
    dialog.config = _FakeConfig(
        {
            "OPENAI_KEY": "sk-test-secret",
            "ROK_YOLO_WEIGHTS": str(yolo_weights),
            "TESSERACT_PATH": str(tesseract_path),
        }
    )

    dialog._run_checks()

    assert dialog._api_status.text() == "OK"
    assert dialog._interception_status.text() == "OK"
    assert dialog._game_status.text() == "OK"
    assert dialog._yolo_status.text() == "OK"
    assert dialog._tesseract_status.text() == "OK"
    dialog.close()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("path", None),
        (None, (False, "Not available")),
        (RuntimeError("download failed"), (False, "download failed")),
    ],
)
def test_yolo_download_worker_reports_results(monkeypatch, qapp, tmp_path, result, expected):
    del qapp
    yolo_result = tmp_path / "weights.pt" if result == "path" else result
    expected_result = expected or (True, str(yolo_result))
    emitted = []

    class _FakeManager:
        def __init__(self, config):
            self.config = config

        def ensure_yolo_weights(self):
            if isinstance(yolo_result, Exception):
                raise yolo_result
            return yolo_result

    monkeypatch.setattr("health_check.ModelManager", _FakeManager)
    worker = _YoloDownloadWorker(_FakeConfig({}))
    worker.finished.connect(lambda success, message: emitted.append((success, message)))

    worker.run()

    assert emitted == [expected_result]


def test_health_check_run_checks_marks_missing_optional_tools(monkeypatch, qapp):
    del qapp
    fake_interception = ModuleType("interception")

    def _raise_interception():
        raise RuntimeError("driver missing")

    fake_interception.auto_capture_devices = _raise_interception
    fake_pygetwindow = ModuleType("pygetwindow")

    def _raise_window_lookup(_title):
        raise RuntimeError("window missing")

    fake_pygetwindow.getWindowsWithTitle = _raise_window_lookup
    monkeypatch.setitem(sys.modules, "interception", fake_interception)
    monkeypatch.setitem(sys.modules, "pygetwindow", fake_pygetwindow)
    monkeypatch.setattr("health_check.ConfigManager", lambda: _FakeConfig({"OPENAI_KEY": "sk-test-secret"}))
    monkeypatch.setattr("health_check.QtCore.QTimer.singleShot", lambda *_args, **_kwargs: None)

    dialog = HealthCheckDialog()
    dialog._run_checks()

    assert dialog._api_status.text() == "OK"
    assert dialog._interception_status.text() == "FAIL"
    assert dialog._game_status.text() == "FAIL"
    assert dialog._yolo_status.text() == "WARN"
    assert dialog._tesseract_status.text() == "WARN"
    assert dialog._yolo_download_btn.isHidden() is False
    dialog.close()


def test_health_check_save_api_key_persists_and_rechecks(monkeypatch, qapp):
    del qapp
    fake_config = _FakeConfig({})
    monkeypatch.setattr("health_check.ConfigManager", lambda: fake_config)
    monkeypatch.setattr("health_check.QtCore.QTimer.singleShot", lambda *_args, **_kwargs: None)

    dialog = HealthCheckDialog()
    reruns = []
    monkeypatch.setattr(dialog, "_run_checks", lambda: reruns.append("run"))
    dialog._api_input.setText("sk-new-secret")

    dialog._save_api_key()

    assert fake_config.saved_updates == [{"OPENAI_KEY": "sk-new-secret"}]
    assert reruns == ["run"]
    dialog.close()


@pytest.mark.parametrize(
    ("success", "message", "expected_text"),
    [
        (True, "weights.pt", "Downloaded"),
        (False, "download failed", "Failed: download failed"),
    ],
)
def test_health_check_on_yolo_download_finished_updates_ui(monkeypatch, qapp, success, message, expected_text):
    del qapp
    fake_config = _FakeConfig({})
    monkeypatch.setattr("health_check.ConfigManager", lambda: fake_config)
    monkeypatch.setattr("health_check.QtCore.QTimer.singleShot", lambda *_args, **_kwargs: None)

    dialog = HealthCheckDialog()
    reruns = []
    monkeypatch.setattr(dialog, "_run_checks", lambda: reruns.append("run"))
    dialog._yolo_download_thread = object()
    dialog._yolo_download_worker = object()

    dialog._on_yolo_download_finished(success, message)

    assert dialog._yolo_download_btn.text() == expected_text
    assert dialog._yolo_download_btn.isEnabled() is True
    assert dialog._yolo_download_thread is None
    assert dialog._yolo_download_worker is None
    assert reruns == ["run"]
    dialog.close()
