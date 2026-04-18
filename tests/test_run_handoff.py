import json
from pathlib import Path

import maintainer_run as maintainer_run_module
import run_handoff as run_handoff_module
from run_handoff import RunRecordSession, reconcile_latest_runtime_run


def test_run_record_session_writes_latest_handoff_and_terminal_once(tmp_path):
    output_dir = tmp_path / "session_logs"
    handoff_dir = tmp_path / "handoff"
    session = RunRecordSession(
        run_kind="runtime_session",
        command_or_mission="Farm safely",
        output_dir=output_dir,
        handoff_dir=handoff_dir,
        metadata={"mission": "Farm safely", "autonomy_level": 1},
    )

    session.record_event("info", detail="Session started.")
    session.record_event("action", action_type="wait", label="wait", outcome="success", source="ai")
    session.mark_terminal("success", "mission_complete", detail="Mission complete.")
    session.mark_terminal("failed", "should_not_replace", detail="ignore")
    path = session.finalize()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["end_reason"] == "mission_complete"
    assert payload["counts"]["actions"] == 1
    assert len([event for event in payload["events"] if event["event_type"] == "terminal"]) == 1
    assert (handoff_dir / "latest_run.json").exists()
    assert (handoff_dir / "latest_run.txt").exists()
    assert path.with_suffix(".ndjson").exists()
    assert path.with_suffix(".log").exists()
    assert path.with_suffix(".err").exists()


def test_reconcile_latest_runtime_run_marks_incomplete_session_interrupted(tmp_path):
    output_dir = tmp_path / "session_logs"
    handoff_dir = tmp_path / "handoff"
    session = RunRecordSession(
        run_kind="runtime_session",
        command_or_mission="Farm safely",
        output_dir=output_dir,
        handoff_dir=handoff_dir,
        metadata={"mission": "Farm safely", "autonomy_level": 2},
    )
    session.record_event("info", detail="Session started.")

    reconciled = reconcile_latest_runtime_run(
        latest_run_path=handoff_dir / "latest_run.json",
        handoff_text_path=handoff_dir / "latest_run.txt",
    )

    assert reconciled is not None
    assert reconciled["status"] == "interrupted"
    assert reconciled["end_reason"] == "previous_run_incomplete"
    latest_payload = json.loads((handoff_dir / "latest_run.json").read_text(encoding="utf-8"))
    assert latest_payload["status"] == "interrupted"
    events = [json.loads(line) for line in session.paths.ndjson_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert events[-1]["event_type"] == "terminal"
    assert events[-1]["end_reason"] == "previous_run_incomplete"


def test_build_preset_command_centralizes_pytest_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(maintainer_run_module, "DEFAULT_TEST_RUNS_DIR", tmp_path / "test_runs")

    command, env, metadata = maintainer_run_module._build_preset_command(
        "pytest",
        ["-m", "integration"],
        "run_123",
    )

    assert command[:3] == [maintainer_run_module.sys.executable, "-m", "pytest"]
    assert "--basetemp" in command
    assert any(arg.startswith("cache_dir=") for arg in command)
    assert Path(env["TMP"]).is_dir()
    assert Path(env["TEMP"]).is_dir()
    assert Path(env["TMPDIR"]).is_dir()
    assert metadata["test_run_root"].endswith("run_123")


def test_cleanup_legacy_test_artifacts_removes_known_patterns(tmp_path):
    legacy_root = tmp_path / ".pytest_tmp_old"
    legacy_root.mkdir()
    nested = tmp_path / "pytest-cache-files-abc"
    nested.mkdir()
    smoke = tmp_path / "data" / "smoke_config_tests" / "pytest_audit"
    smoke.mkdir(parents=True)

    removed = run_handoff_module.cleanup_legacy_test_artifacts(tmp_path)

    assert legacy_root in removed
    assert nested in removed
    assert smoke.parent in removed
    assert not legacy_root.exists()
    assert not nested.exists()
    assert not smoke.parent.exists()
