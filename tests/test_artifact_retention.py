from datetime import datetime
from pathlib import Path

import detection_dataset as detection_dataset_module
import diagnostic_screenshot as diagnostic_screenshot_module
from artifact_retention import ArtifactRetentionManager, ArtifactRetentionPolicy
from detection_dataset import DetectionDataset
from diagnostic_screenshot import save_diagnostic_screenshot
from session_logger import SessionLogger


def test_artifact_retention_prunes_oldest_group_by_stem(tmp_path):
    older_png = tmp_path / "older.png"
    older_log = tmp_path / "older.log"
    newer_png = tmp_path / "newer.png"
    for path in (older_png, older_log, newer_png):
        path.write_text(path.name, encoding="utf-8")

    older_mtime = 1_700_000_000
    newer_mtime = older_mtime + 100
    older_png.touch()
    older_log.touch()
    newer_png.touch()
    older_png.stat()
    newer_png.stat()

    import os

    os.utime(older_png, (older_mtime, older_mtime))
    os.utime(older_log, (older_mtime, older_mtime))
    os.utime(newer_png, (newer_mtime, newer_mtime))

    removed = ArtifactRetentionManager().prune_directory(tmp_path, ArtifactRetentionPolicy(max_groups=1))

    assert set(removed) == {older_png, older_log}
    assert not older_png.exists()
    assert newer_png.exists()


def test_session_logger_finalize_prunes_oldest_logs(tmp_path):
    retention_manager = ArtifactRetentionManager()
    retention_policy = ArtifactRetentionPolicy(max_groups=1)
    first = SessionLogger(
        mission="first",
        output_dir=tmp_path,
        retention_manager=retention_manager,
        retention_policy=retention_policy,
    )
    first._start_datetime = datetime(2024, 1, 1, 0, 0, 0)
    first_path = first.finalize()

    second = SessionLogger(
        mission="second",
        output_dir=tmp_path,
        retention_manager=retention_manager,
        retention_policy=retention_policy,
    )
    second._start_datetime = datetime(2024, 1, 1, 0, 0, 1)
    second_path = second.finalize()

    assert first_path is not None
    assert second_path is not None
    assert not first_path.exists()
    assert not first_path.with_suffix(".txt").exists()
    assert second_path.exists()
    assert second_path.with_suffix(".txt").exists()


def test_session_logger_writes_text_report(tmp_path):
    logger = SessionLogger(mission="farm", output_dir=tmp_path)
    logger.record_planner_rejection(
        reason="confidence_below_threshold:0.460<0.700",
        action_type="click",
        label="Resource node",
        target_id="ocr_1",
        confidence=0.46,
    )
    path = logger.finalize()

    assert path is not None
    report = path.with_suffix(".txt").read_text(encoding="utf-8")
    assert "OSROKBOT Session Report" in report
    assert "Planner rejections: 1" in report
    assert "Resource node" in report


def test_detection_dataset_retention_prunes_oldest_export_group(monkeypatch, tmp_path):
    monkeypatch.setattr(
        detection_dataset_module,
        "DEFAULT_DATASET_RETENTION",
        ArtifactRetentionPolicy(max_groups=1),
    )
    source = tmp_path / "screen.png"
    source.write_bytes(b"png")
    dataset = DetectionDataset(output_dir=tmp_path / "dataset")

    first = dataset.export_stub(source, "first")
    second = dataset.export_stub(source, "second")

    assert first is not None
    assert second is not None
    assert not first.exists()
    assert second.exists()
    assert len(list((tmp_path / "dataset").glob("second_*"))) == 3


def test_diagnostic_screenshot_retention_prunes_oldest_group(monkeypatch, tmp_path):
    monkeypatch.setattr(
        diagnostic_screenshot_module,
        "DEFAULT_DIAGNOSTICS_RETENTION",
        ArtifactRetentionPolicy(max_groups=1),
    )

    class _FakeScreenshot:
        def __init__(self, text):
            self.text = text

        def save(self, path):
            Path(path).write_text(self.text, encoding="utf-8")

    first = save_diagnostic_screenshot(_FakeScreenshot("first"), label="older", diagnostics_dir=tmp_path)
    second = save_diagnostic_screenshot(_FakeScreenshot("second"), label="newer", diagnostics_dir=tmp_path)

    assert first is not None
    assert second is not None
    assert not first.exists()
    assert second.exists()
