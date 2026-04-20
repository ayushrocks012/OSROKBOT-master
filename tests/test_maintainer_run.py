from pathlib import Path
from types import SimpleNamespace

import maintainer_run as maintainer_run_module


def test_format_milestone_omits_empty_fields():
    milestone = maintainer_run_module._format_milestone(
        "RUN START",
        "run-123",
        "maintainer_command",
        preset="pytest",
        empty="",
        none_value=None,
    )

    assert milestone.startswith('RUN START run_id="run-123" run_kind="maintainer_command"')
    assert 'preset="pytest"' in milestone
    assert "empty=" not in milestone
    assert "none_value=" not in milestone


def test_parse_failing_tests_deduplicates_failed_and_error_entries():
    lines = [
        "FAILED tests/test_one.py::test_alpha - boom",
        "ERROR tests/test_two.py::test_beta - bad fixture",
        "FAILED tests/test_one.py::test_alpha - boom again",
    ]

    assert maintainer_run_module._parse_failing_tests(lines) == [
        "tests/test_one.py::test_alpha",
        "tests/test_two.py::test_beta",
    ]


def test_parse_failed_checks_extracts_fail_lines():
    lines = ["[OK] first", "[FAIL] docs missing", "noise", "[FAIL] bad import"]

    assert maintainer_run_module._parse_failed_checks(lines) == [
        "docs missing",
        "bad import",
    ]


def test_has_option_matches_plain_and_assignment_forms():
    arguments = ["--basetemp", "tmp", "-o", "cache_dir=.cache", "--flag=value"]

    assert maintainer_run_module._has_option(arguments, "--basetemp") is True
    assert maintainer_run_module._has_option(arguments, "--flag") is True
    assert maintainer_run_module._has_option(arguments, "--missing") is False


def test_build_preset_command_supports_repo_hygiene():
    command, env, metadata = maintainer_run_module._build_preset_command("repo-hygiene", [], "run_123")

    assert command == [maintainer_run_module.sys.executable, "tools/check_repo_hygiene.py"]
    assert metadata["preset"] == "repo-hygiene"
    assert isinstance(env, dict)


def test_build_preset_command_for_pytest_sets_artifact_paths(monkeypatch, tmp_path):
    test_paths = SimpleNamespace(
        root=tmp_path / "root",
        temp_root=tmp_path / "temp",
        pytest_temp=tmp_path / "pytest-temp",
        pytest_cache=tmp_path / "pytest-cache",
    )
    monkeypatch.setattr(maintainer_run_module, "prepare_test_run_paths", lambda run_id, base_dir=None: test_paths)

    command, env, metadata = maintainer_run_module._build_preset_command("pytest", ["tests/test_one.py"], "run_123")

    assert command[:4] == [maintainer_run_module.sys.executable, "-m", "pytest", "tests/test_one.py"]
    assert "--basetemp" in command
    assert any(str(test_paths.pytest_cache) in item for item in command)
    assert env["TMP"] == str(test_paths.temp_root)
    assert env["TEMP"] == str(test_paths.temp_root)
    assert env["TMPDIR"] == str(test_paths.temp_root)
    assert metadata["test_run_root"] == str(test_paths.root)
    assert Path(metadata["pytest_temp"]).is_dir()
    assert Path(metadata["pytest_cache"]).is_dir()


def test_main_strips_double_dash_before_delegating(monkeypatch):
    captured = {}

    def _fake_run_preset(preset, extra_args):
        captured["preset"] = preset
        captured["extra_args"] = list(extra_args)
        return 7

    monkeypatch.setattr(maintainer_run_module, "run_preset", _fake_run_preset)

    result = maintainer_run_module.main(["pytest", "--", "-k", "smoke"])

    assert result == 7
    assert captured == {"preset": "pytest", "extra_args": ["-k", "smoke"]}
