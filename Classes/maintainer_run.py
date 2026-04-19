"""Maintainer command wrapper with canonical run handoff artifacts.

Use this module through the PowerShell entrypoint so documented maintainer
commands emit deterministic milestone lines, grouped history files, and
contained pytest artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from logging_config import get_logger, scoped_log_context
from run_handoff import (
    DEFAULT_TEST_RUNS_DIR,
    RunRecordSession,
    build_run_id,
    cleanup_legacy_test_artifacts,
    prepare_test_run_paths,
    prune_test_run_artifacts,
)
from security_utils import atomic_write_text, redact_secret

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGGER = get_logger(__name__)


def _format_milestone(prefix: str, run_id: str, run_kind: str, **fields: Any) -> str:
    parts = [prefix, f"run_id={json.dumps(run_id)}", f"run_kind={json.dumps(run_kind)}"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={json.dumps(redact_secret(value))}")
    return " ".join(parts)


def _print_milestone(prefix: str, session: RunRecordSession, **fields: Any) -> None:
    print(_format_milestone(prefix, session.run_id, session.run_kind, **fields), flush=True)


def _parse_failing_tests(lines: list[str]) -> list[str]:
    pattern = re.compile(r"^(?:FAILED|ERROR)\s+(\S+::\S+)")
    failures: list[str] = []
    for line in lines:
        match = pattern.match(line.strip())
        if match:
            failures.append(match.group(1))
    return sorted(dict.fromkeys(failures))


def _parse_failed_checks(lines: list[str]) -> list[str]:
    failed_checks: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[FAIL]"):
            failed_checks.append(stripped.removeprefix("[FAIL]").strip())
    return failed_checks


def _copy_run_summary_to_test_root(run_path: Path, test_root: Path) -> None:
    payload = run_path.read_text(encoding="utf-8")
    summary_text = run_path.with_suffix(".txt").read_text(encoding="utf-8")
    atomic_write_text(test_root / "latest_run.json", payload)
    atomic_write_text(test_root / "latest_run.txt", summary_text)


def _has_option(arguments: list[str], option: str) -> bool:
    return any(argument == option or argument.startswith(f"{option}=") for argument in arguments)


def _build_preset_command(preset: str, extra_args: list[str], run_id: str) -> tuple[list[str], dict[str, str], dict[str, Any]]:
    env = os.environ.copy()
    metadata: dict[str, Any] = {"preset": preset}
    if preset == "pytest":
        test_paths = prepare_test_run_paths(run_id, base_dir=DEFAULT_TEST_RUNS_DIR)
        test_paths.temp_root.mkdir(parents=True, exist_ok=True)
        test_paths.pytest_temp.mkdir(parents=True, exist_ok=True)
        test_paths.pytest_cache.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "TMP": str(test_paths.temp_root),
                "TEMP": str(test_paths.temp_root),
                "TMPDIR": str(test_paths.temp_root),
            }
        )
        command = [sys.executable, "-m", "pytest", *extra_args]
        if not _has_option(extra_args, "--basetemp"):
            command.extend(["--basetemp", str(test_paths.pytest_temp)])
        if not any(argument.startswith("cache_dir=") for argument in extra_args):
            command.extend(["-o", f"cache_dir={test_paths.pytest_cache}"])
        metadata["test_run_root"] = str(test_paths.root)
        metadata["pytest_temp"] = str(test_paths.pytest_temp)
        metadata["pytest_cache"] = str(test_paths.pytest_cache)
        return command, env, metadata
    if preset == "verify-integrity":
        return [sys.executable, "verify_integrity.py", *extra_args], env, metadata
    if preset == "verify-docs":
        return [sys.executable, "verify_docs.py", *extra_args], env, metadata
    if preset == "mypy":
        return [sys.executable, "-m", "mypy", *extra_args], env, metadata
    if preset == "watchdog-once":
        return [sys.executable, "watchdog.py", "--once", *extra_args], env, metadata
    if preset == "ui":
        return [sys.executable, "Classes\\UI.py", *extra_args], env, metadata
    raise ValueError(f"Unsupported preset: {preset}")


def _stream_output(session: RunRecordSession, stream_name: str, handle, sink: list[str]) -> None:
    prefix = "RUN ERROR" if stream_name == "stderr" else "RUN EVENT"
    for raw_line in iter(handle.readline, ""):
        clean_line = redact_secret(raw_line.rstrip("\n"))
        if not clean_line:
            continue
        sink.append(clean_line)
        session.append_output_line(stream_name, clean_line)
        _print_milestone(prefix, session, stream=stream_name, text=clean_line)
    handle.close()


def _run_subprocess(session: RunRecordSession, command: list[str], env: dict[str, str]) -> tuple[int, list[str], list[str]]:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = threading.Thread(
        target=_stream_output,
        args=(session, "stdout", process.stdout, stdout_lines),
        name="MaintainerRun-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_stream_output,
        args=(session, "stderr", process.stderr, stderr_lines),
        name="MaintainerRun-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    exit_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return exit_code, stdout_lines, stderr_lines


def _cleanup_command(session: RunRecordSession) -> int:
    removed = cleanup_legacy_test_artifacts()
    pruned = prune_test_run_artifacts()
    session.record_event(
        "info",
        detail=f"Removed {len(removed)} legacy test artifact paths and pruned {len(pruned)} centralized test runs.",
    )
    for path in removed:
        _print_milestone("RUN EVENT", session, removed=str(path))
    for path in pruned:
        _print_milestone("RUN EVENT", session, pruned=str(path))
    session.update_metadata(
        command_summary={
            "removed_paths": [str(path) for path in removed],
            "pruned_runs": [str(path) for path in pruned],
        }
    )
    session.mark_terminal(
        "success",
        "cleanup_completed",
        detail=f"Removed {len(removed)} legacy paths and pruned {len(pruned)} centralized test runs.",
    )
    session_path = session.finalize()
    _print_milestone("RUN END", session, status="success", end_reason="cleanup_completed", summary=str(session_path))
    return 0


def run_preset(preset: str, extra_args: list[str]) -> int:
    """Execute one documented maintainer preset and write handoff artifacts."""

    if preset == "cleanup-test-artifacts":
        session = RunRecordSession(
            run_kind="maintainer_command",
            command_or_mission="cleanup-test-artifacts",
            metadata={"command_arguments": extra_args},
        )
        with scoped_log_context(run_id=session.run_id, session_id=session.run_id, run_kind=session.run_kind):
            _print_milestone("RUN START", session, preset=preset, command="cleanup-test-artifacts")
            return _cleanup_command(session)

    run_id = build_run_id("maintainer_command")
    command, env, metadata = _build_preset_command(preset, extra_args, run_id)
    display_command = " ".join(command)
    session = RunRecordSession(
        run_kind="maintainer_command",
        command_or_mission=display_command,
        run_id=run_id,
        metadata={"command_arguments": extra_args, **metadata},
    )
    with scoped_log_context(run_id=session.run_id, session_id=session.run_id, run_kind=session.run_kind):
        session.record_event("info", detail=f"Maintainer preset started: {preset}")
        _print_milestone("RUN START", session, preset=preset, command=display_command)
        exit_code, stdout_lines, stderr_lines = _run_subprocess(session, command, env)

        command_summary: dict[str, Any] = {}
        if preset == "pytest":
            command_summary["failing_tests"] = _parse_failing_tests(stdout_lines + stderr_lines)
        if preset in {"verify-integrity", "verify-docs"}:
            command_summary["failed_checks"] = _parse_failed_checks(stdout_lines + stderr_lines)

        session.update_metadata(exit_code=exit_code, command_summary=command_summary)
        if exit_code == 0:
            session.mark_terminal("success", "command_completed", detail=f"{preset} completed successfully.")
            status = "success"
            end_reason = "command_completed"
        else:
            session.mark_terminal("failed", f"exit_code_{exit_code}", detail=f"{preset} exited with code {exit_code}.")
            status = "failed"
            end_reason = f"exit_code_{exit_code}"

        session_path = session.finalize()
        test_run_root = metadata.get("test_run_root")
        if test_run_root:
            test_root_path = Path(str(test_run_root))
            test_root_path.mkdir(parents=True, exist_ok=True)
            _copy_run_summary_to_test_root(session_path, test_root_path)
            prune_test_run_artifacts()
        _print_milestone(
            "RUN END",
            session,
            status=status,
            end_reason=end_reason,
            exit_code=str(exit_code),
            summary=str(session_path),
        )
        return exit_code


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser for the maintainer wrapper."""

    parser = argparse.ArgumentParser(description="Run documented maintainer commands with grouped handoff artifacts.")
    parser.add_argument(
        "preset",
        choices=[
            "pytest",
            "verify-integrity",
            "verify-docs",
            "mypy",
            "watchdog-once",
            "ui",
            "cleanup-test-artifacts",
        ],
    )
    parser.add_argument("extra_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the maintainer wrapper."""

    args = build_parser().parse_args(argv)
    extra_args = list(args.extra_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    return int(run_preset(args.preset, extra_args))


if __name__ == "__main__":
    raise SystemExit(main())
