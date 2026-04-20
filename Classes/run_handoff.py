"""Shared run-record, handoff, and test-artifact helpers.

This module owns the canonical per-run artifact contract used by both runtime
automation sessions and maintainer command wrappers. It writes grouped history
files, refreshes the latest handoff payload for the next agent, and keeps test
artifacts contained under one ignored root.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_retention import ArtifactRetentionManager, ArtifactRetentionPolicy, policy_from_environment
from logging_config import get_logger
from runtime_journal import reconcile_runtime_journal_artifacts
from security_utils import atomic_write_text, redact_secret

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SESSION_LOGS_DIR = PROJECT_ROOT / "data" / "session_logs"
DEFAULT_HANDOFF_DIR = PROJECT_ROOT / "data" / "handoff"
DEFAULT_LATEST_RUN_JSON = DEFAULT_HANDOFF_DIR / "latest_run.json"
DEFAULT_LATEST_RUN_TEXT = DEFAULT_HANDOFF_DIR / "latest_run.txt"
DEFAULT_RUNTIME_LOG_PATH = PROJECT_ROOT / "data" / "logs" / "osrokbot.log"
DEFAULT_HEARTBEAT_PATH = PROJECT_ROOT / "data" / "heartbeat.json"
DEFAULT_PLANNER_SCREENSHOT_PATH = PROJECT_ROOT / "data" / "planner_latest.png"
DEFAULT_DIAGNOSTICS_DIR = PROJECT_ROOT / "diagnostics"
DEFAULT_TEST_RUNS_DIR = PROJECT_ROOT / ".artifacts" / "test_runs"
DEFAULT_LIVE_SNAPSHOT_INTERVAL_SECONDS = 1.0
DEFAULT_SESSION_RETENTION = policy_from_environment(
    max_groups_env="ROK_SESSION_LOG_MAX_FILES",
    max_age_days_env="ROK_SESSION_LOG_MAX_AGE_DAYS",
    default_max_groups=200,
    default_max_age_days=30.0,
)
DEFAULT_TEST_SUCCESS_RETENTION = policy_from_environment(
    max_groups_env="ROK_TEST_RUN_SUCCESS_MAX_FILES",
    max_age_days_env="ROK_TEST_RUN_SUCCESS_MAX_AGE_DAYS",
    default_max_groups=10,
    default_max_age_days=7.0,
)
DEFAULT_TEST_FAILURE_RETENTION = policy_from_environment(
    max_groups_env="ROK_TEST_RUN_FAILURE_MAX_FILES",
    max_age_days_env="ROK_TEST_RUN_FAILURE_MAX_AGE_DAYS",
    default_max_groups=20,
    default_max_age_days=30.0,
)
LOGGER = get_logger(__name__)

_TERMINAL_STATUSES = {"success", "failed", "interrupted", "partial"}
_WARNING_EVENT_TYPES = {"warning", "planner_rejection", "rejection"}
_KEY_EVENT_TYPES = {
    "action",
    "approval",
    "rejection",
    "correction",
    "error",
    "warning",
    "captcha",
    "planner_rejection",
    "decision",
    "terminal",
    "info",
}
_LEGACY_TEST_ARTIFACT_PATTERNS = (
    ".pytest_tmp*",
    ".pytest_cache*",
    "pytest_tmp*",
    "pytest-cache-files-*",
    "data/pytest-cache-files-*",
    "data/smoke_config_tests",
)
_ARTIFACT_TEXT_KEYS = (
    "summary",
    "json",
    "transcript",
    "stderr",
    "event_stream",
    "runtime_journal",
    "runtime_checkpoint",
    "diagnostics",
    "heartbeat",
    "planner_screenshot",
)
_DIRECT_COUNT_FIELDS = {
    "approval": "approvals",
    "correction": "corrections",
    "warning": "warnings",
    "error": "errors",
    "captcha": "captchas",
    "timing": "timings",
}
_WARNING_COUNT_FIELDS = {
    "planner_rejection": "planner_rejections",
    "rejection": "rejections",
}


def _now() -> datetime:
    """Return the current wall-clock timestamp for run artifacts."""

    return datetime.now()


def _isoformat(value: datetime) -> str:
    """Serialize timestamps using second precision for operator-facing artifacts."""

    return value.isoformat(timespec="seconds")


def _safe_run_fragment(value: str) -> str:
    """Normalize an arbitrary run identifier fragment for safe filesystem use."""

    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    return cleaned.strip("_") or "run"


def _duration_text(duration_seconds: float) -> str:
    """Render a compact duration string for text handoff output."""

    total_seconds = max(0, int(round(duration_seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _append_text(path: Path, text: str) -> None:
    """Append text to one artifact file, creating parent directories as needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _append_json_line(path: Path, payload: dict[str, Any]) -> None:
    """Append one JSON object as NDJSON to an artifact stream."""

    _append_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read one JSON file, returning `None` for missing or invalid payloads."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_events(path: Path) -> list[dict[str, Any]]:
    """Read a per-run NDJSON event stream into structured event dicts."""

    events: list[dict[str, Any]] = []
    if not path.is_file():
        return events
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            payload = json.loads(raw_line)
            if isinstance(payload, dict):
                events.append(payload)
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Unable to read run event stream %s: %s", path, exc)
    return events


@dataclass(frozen=True)
class RunArtifactPaths:
    """One grouped set of per-run artifact paths."""

    directory: Path
    stem: str
    json_path: Path
    txt_path: Path
    log_path: Path
    err_path: Path
    ndjson_path: Path


@dataclass(frozen=True)
class TestRunPaths:
    """One contained directory layout for a pytest run."""

    root: Path
    temp_root: Path
    pytest_temp: Path
    pytest_cache: Path


def build_run_id(prefix: str) -> str:
    """Return a timestamped run identifier safe for filenames."""

    return f"{_safe_run_fragment(prefix)}_{_now().strftime('%Y%m%d_%H%M%S_%f')}"


def build_run_artifact_paths(base_dir: Path, stem: str) -> RunArtifactPaths:
    """Return the grouped history files for one run stem."""

    return RunArtifactPaths(
        directory=base_dir,
        stem=stem,
        json_path=base_dir / f"{stem}.json",
        txt_path=base_dir / f"{stem}.txt",
        log_path=base_dir / f"{stem}.log",
        err_path=base_dir / f"{stem}.err",
        ndjson_path=base_dir / f"{stem}.ndjson",
    )


def prepare_test_run_paths(run_id: str, base_dir: Path = DEFAULT_TEST_RUNS_DIR) -> TestRunPaths:
    """Return the contained temp/cache layout for one pytest run."""

    root = Path(base_dir) / run_id
    return TestRunPaths(
        root=root,
        temp_root=root / "t",
        pytest_temp=root / "pt",
        pytest_cache=root / "pc",
    )


def _event_log_line(event: dict[str, Any]) -> str:
    """Render one structured event as a single plain-text log line."""

    elapsed = float(event.get("elapsed_seconds", 0.0) or 0.0)
    bits = [
        str(event.get("timestamp", "")),
        f"+{elapsed:0.1f}s",
        str(event.get("severity", "INFO")).upper(),
        str(event.get("event_type", "info")),
    ]
    for key in ("stage", "action_type", "label", "target_id", "status", "end_reason", "detail"):
        value = event.get(key)
        if _is_blank(value):
            continue
        bits.append(f"{key}={value}")
    return " ".join(bits).rstrip() + "\n"


def _coerce_status(value: str | None) -> str:
    """Normalize handoff status values to the supported terminal set."""

    text = str(value or "partial").strip().lower()
    return text if text in _TERMINAL_STATUSES else "partial"


def _is_blank(value: Any) -> bool:
    """Return whether one event or metadata value should be treated as empty."""

    return value is None or value == ""


def _update_action_counts(counts: dict[str, int], event: dict[str, Any]) -> None:
    """Update action, API-call, and memory-hit counters for one action event."""

    counts["actions"] += 1
    source = str(event.get("source", ""))
    if source == "memory":
        counts["memory_hits"] += 1
    elif source:
        counts["api_calls"] += 1


def _increment_event_counts(counts: dict[str, int], event_type: str) -> None:
    """Apply direct per-event counter increments for one event type."""

    if event_type in _DIRECT_COUNT_FIELDS:
        counts[_DIRECT_COUNT_FIELDS[event_type]] += 1
        return
    if event_type in _WARNING_COUNT_FIELDS:
        counts[_WARNING_COUNT_FIELDS[event_type]] += 1
        counts["warnings"] += 1


def _build_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate the event counters shown in structured and text handoff output."""

    counts = {
        "actions": 0,
        "approvals": 0,
        "corrections": 0,
        "planner_rejections": 0,
        "warnings": 0,
        "errors": 0,
        "captchas": 0,
        "rejections": 0,
        "api_calls": 0,
        "memory_hits": 0,
        "timings": 0,
    }
    for event in events:
        event_type = str(event.get("event_type", ""))
        if event_type == "action":
            _update_action_counts(counts, event)
            continue
        _increment_event_counts(counts, event_type)
    return counts


def _build_top_errors(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the most common error details for concise operator handoff."""

    grouped: Counter[tuple[str, str]] = Counter()
    last_seen: dict[tuple[str, str], str] = {}
    for event in events:
        if str(event.get("event_type", "")) != "error":
            continue
        stage = str(event.get("stage", "") or "")
        detail = str(event.get("detail", "") or "(no detail)")
        key = (stage, detail)
        grouped[key] += 1
        last_seen[key] = str(event.get("timestamp", "") or "")
    top_errors: list[dict[str, Any]] = []
    for (stage, detail), count in grouped.most_common(5):
        top_errors.append(
            {
                "stage": stage,
                "detail": detail,
                "count": count,
                "last_seen": last_seen[(stage, detail)],
            }
        )
    return top_errors


def _build_key_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the most recent operator-relevant events for text handoff output."""

    key_events: list[dict[str, Any]] = []
    for event in events:
        event_type = str(event.get("event_type", ""))
        if event_type not in _KEY_EVENT_TYPES:
            continue
        key_events.append(
            {
                "timestamp": event.get("timestamp", ""),
                "elapsed_seconds": event.get("elapsed_seconds", 0.0),
                "event_type": event_type,
                "detail": event.get("detail", ""),
                "action_type": event.get("action_type", ""),
                "label": event.get("label", ""),
                "status": event.get("status", ""),
                "end_reason": event.get("end_reason", ""),
            }
        )
    return key_events[-12:]


def _latest_event_of_type(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    """Return the newest event of one type from the event stream."""

    for event in reversed(events):
        if str(event.get("event_type", "")) == event_type:
            return event
    return None


def _last_successful_action(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the last successful action summary for quick operator context."""

    for event in reversed(events):
        if str(event.get("event_type", "")) != "action":
            continue
        if str(event.get("outcome", "")) not in {"success", "corrected"}:
            continue
        return {
            "timestamp": event.get("timestamp", ""),
            "action_type": event.get("action_type", ""),
            "label": event.get("label", ""),
            "target_id": event.get("target_id", ""),
            "outcome": event.get("outcome", ""),
        }
    return None


def _final_state(events: list[dict[str, Any]]) -> str:
    """Resolve the last meaningful state description for one run record."""

    latest_state = _latest_event_of_type(events, "state")
    if latest_state:
        return str(latest_state.get("state_text", "") or "")
    terminal = _latest_event_of_type(events, "terminal")
    if terminal:
        return str(terminal.get("detail", "") or "")
    return ""


def _success_next_actions(counts: dict[str, Any]) -> list[str]:
    """Return follow-up guidance for a successful run."""

    if int(counts.get("warnings", 0) or 0):
        return ["Review warnings in the handoff text before trusting the run outcome."]
    return ["Read latest_run.txt for the concise handoff, then inspect the history files only if needed."]


def _failed_next_actions(record: dict[str, Any], run_kind: str) -> list[str]:
    """Return follow-up guidance for a failed run."""

    actions = ["Open the stderr artifact first, then inspect the structured JSON for the failing boundary."]
    if run_kind == "maintainer_command" and record.get("command_summary", {}).get("failing_tests"):
        actions.append("Start with the failing pytest node ids listed in command_summary.failing_tests.")
    if run_kind == "runtime_session":
        actions.append("Inspect diagnostics and planner_latest.png before changing the mission or approval settings.")
    return actions


def _resume_boundary_actions(resume_checkpoint: dict[str, Any]) -> list[str]:
    """Return follow-up guidance for interrupted runs with journal resume data."""

    if not bool(resume_checkpoint.get("verified", False)):
        return ["Do not resume automatically until the runtime journal checkpoint verifies cleanly."]

    resume_state = str(
        resume_checkpoint.get("next_state", "") or resume_checkpoint.get("state_name", "") or "the last committed state"
    )
    actions = [f"Resume from {resume_state} only after re-observing the screen."]
    if int(resume_checkpoint.get("pending_tail_count", 0) or 0):
        actions.append("Treat the uncommitted journal tail as incomplete work and reacquire the current UI before any new input.")
    return actions


def _default_next_actions(record: dict[str, Any]) -> list[str]:
    """Return the operator next-action checklist for one run record."""

    actions: list[str] = []
    status = str(record.get("status", "partial"))
    run_kind = str(record.get("run_kind", ""))
    counts = record.get("counts", {})
    artifacts = record.get("artifacts", {})
    resume_checkpoint = record.get("resume_checkpoint", {})
    if status == "success":
        actions.extend(_success_next_actions(counts))
    elif status == "failed":
        actions.extend(_failed_next_actions(record, run_kind))
    else:
        if run_kind == "runtime_session" and isinstance(resume_checkpoint, dict) and resume_checkpoint:
            actions.extend(_resume_boundary_actions(resume_checkpoint))
        else:
            actions.append("Confirm whether the run was stopped intentionally before resuming or starting a new session.")
    if artifacts.get("runtime_log"):
        actions.append("Use the aggregate runtime log only for extra context after the per-run artifacts.")
    return actions[:4]


def _what_ran_lines(record: dict[str, Any]) -> list[str]:
    """Render the stable 'What Ran' section of the latest-run text report."""

    lines = [
        "What Ran",
        f"- Run ID: {record.get('run_id', '')}",
        f"- Kind: {record.get('run_kind', '')}",
        f"- Command Or Mission: {record.get('command_or_mission', '')}",
    ]
    mission = str(record.get("mission", "") or "")
    autonomy_level = record.get("autonomy_level")
    if mission:
        lines.append(f"- Mission: {mission}")
    if not _is_blank(autonomy_level):
        lines.append(f"- Autonomy: L{autonomy_level}")
    return lines


def _outcome_lines(record: dict[str, Any]) -> list[str]:
    """Render the stable 'Outcome' section of the latest-run text report."""

    lines = [
        "",
        "Outcome",
        f"- Status: {record.get('status', '')}",
        f"- End Reason: {record.get('end_reason', '')}",
        f"- Started: {record.get('started_at', '')}",
        f"- Ended: {record.get('ended_at', '')}",
        f"- Duration: {_duration_text(float(record.get('duration_seconds', 0.0) or 0.0))}",
    ]
    exit_code = record.get("exit_code")
    if exit_code is not None:
        lines.append(f"- Exit Code: {exit_code}")
    final_state = str(record.get("final_state", "") or "")
    if final_state:
        lines.append(f"- Final State: {final_state}")
    return lines


def _resume_boundary_lines(record: dict[str, Any]) -> list[str]:
    """Render the optional runtime resume-boundary section for latest-run text."""

    resume_checkpoint = record.get("resume_checkpoint", {})
    if not isinstance(resume_checkpoint, dict) or not resume_checkpoint:
        return []

    return [
        "",
        "Resume Boundary",
        f"- Verified: {resume_checkpoint.get('verified', False)}",
        *_resume_boundary_detail_lines(resume_checkpoint),
        "- Policy: Re-observe the screen before any new input.",
    ]


def _resume_boundary_detail_lines(resume_checkpoint: dict[str, Any]) -> list[str]:
    """Return the non-header detail lines for the resume-boundary section."""

    lines: list[str] = []
    resume_state = _resume_boundary_state(resume_checkpoint)
    if resume_state:
        lines.append(f"- Resume From State: {resume_state}")
    committed_at = str(resume_checkpoint.get("last_committed_at", "") or "")
    if committed_at:
        lines.append(f"- Last Commit: {committed_at}")
    lines.append(f"- Pending Tail Count: {int(resume_checkpoint.get('pending_tail_count', 0) or 0)}")
    pending_events_line = _resume_boundary_pending_events_line(resume_checkpoint)
    if pending_events_line:
        lines.append(pending_events_line)
    return lines


def _resume_boundary_state(resume_checkpoint: dict[str, Any]) -> str:
    """Return the state name shown in the resume-boundary section."""

    return str(resume_checkpoint.get("next_state", "") or resume_checkpoint.get("state_name", "") or "")


def _resume_boundary_pending_events_line(resume_checkpoint: dict[str, Any]) -> str:
    """Return the formatted pending-tail event line for the resume boundary."""

    pending_tail_events = resume_checkpoint.get("pending_tail_events", [])
    if not isinstance(pending_tail_events, list) or not pending_tail_events:
        return ""
    return f"- Pending Tail Events: {', '.join(str(item) for item in pending_tail_events)}"


def _key_event_lines(record: dict[str, Any]) -> list[str]:
    """Render the condensed event timeline section for latest-run text."""

    lines = ["", "Key Events"]
    key_events = record.get("key_events", [])
    if not key_events:
        lines.append("- No key events recorded.")
        return lines

    for event in key_events:
        detail_bits = [
            str(event.get("event_type", "")),
            str(event.get("action_type", "") or ""),
            str(event.get("label", "") or ""),
            str(event.get("detail", "") or ""),
            str(event.get("status", "") or ""),
            str(event.get("end_reason", "") or ""),
        ]
        detail = " | ".join(bit for bit in detail_bits if bit)
        lines.append(f"- {event.get('timestamp', '')}: {detail}")
    return lines


def _error_warning_lines(record: dict[str, Any]) -> list[str]:
    """Render the error and warning summary section for latest-run text."""

    lines = ["", "Errors / Warnings"]
    top_errors = record.get("top_errors", [])
    if top_errors:
        for item in top_errors:
            stage_prefix = f"[{item.get('stage', '')}] " if item.get("stage") else ""
            lines.append(f"- {stage_prefix}{item.get('detail', '')} (count={item.get('count', 1)})")
    else:
        lines.append("- No error events recorded.")
    lines.append(f"- Warning Count: {int(record.get('counts', {}).get('warnings', 0) or 0)}")

    command_summary = record.get("command_summary", {})
    failing_tests = command_summary.get("failing_tests", [])
    if failing_tests:
        lines.append(f"- Failing Tests: {', '.join(failing_tests)}")
    failed_checks = command_summary.get("failed_checks", [])
    if failed_checks:
        lines.append(f"- Failed Checks: {', '.join(failed_checks)}")
    return lines


def _artifact_lines(record: dict[str, Any]) -> list[str]:
    """Render the artifact-path section for latest-run text."""

    lines = ["", "Artifacts"]
    artifacts = record.get("artifacts", {})
    for key in _ARTIFACT_TEXT_KEYS:
        value = artifacts.get(key)
        if value:
            lines.append(f"- {key}: {value}")
    return lines


def _next_action_lines(record: dict[str, Any]) -> list[str]:
    """Render the operator checklist section for latest-run text."""

    lines = ["", "Next Actions"]
    for action in record.get("next_actions", []):
        lines.append(f"- {action}")
    return lines


def build_run_record(
    *,
    run_id: str,
    run_kind: str,
    command_or_mission: str,
    started_at: str,
    ended_at: str,
    status: str,
    end_reason: str,
    events: list[dict[str, Any]],
    artifact_paths: RunArtifactPaths,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical structured payload for one run."""

    metadata = dict(metadata or {})
    status = _coerce_status(status)
    started_dt = datetime.fromisoformat(started_at)
    ended_dt = datetime.fromisoformat(ended_at)
    duration_seconds = max(0.0, round((ended_dt - started_dt).total_seconds(), 1))
    counts = _build_counts(events)
    latest_decision = _latest_event_of_type(events, "decision")
    artifacts = {
        "summary": str(artifact_paths.txt_path),
        "json": str(artifact_paths.json_path),
        "transcript": str(artifact_paths.log_path),
        "stderr": str(artifact_paths.err_path),
        "event_stream": str(artifact_paths.ndjson_path),
        "runtime_log": str(metadata.get("runtime_log_path", DEFAULT_RUNTIME_LOG_PATH)),
        "diagnostics": str(metadata.get("diagnostics_path", DEFAULT_DIAGNOSTICS_DIR)),
        "heartbeat": str(metadata.get("heartbeat_path", DEFAULT_HEARTBEAT_PATH)),
        "planner_screenshot": str(metadata.get("planner_screenshot_path", DEFAULT_PLANNER_SCREENSHOT_PATH)),
        "runtime_journal": str(metadata.get("runtime_journal_path", "")),
        "runtime_checkpoint": str(metadata.get("runtime_checkpoint_path", "")),
    }
    command_summary = metadata.get("command_summary", {})
    resume_checkpoint = metadata.get("resume_checkpoint")
    journal_integrity = metadata.get("journal_integrity", {})
    record = {
        "run_id": run_id,
        "run_kind": run_kind,
        "command_or_mission": command_or_mission,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "status": status,
        "end_reason": end_reason,
        "exit_code": metadata.get("exit_code"),
        "mission": metadata.get("mission", command_or_mission if run_kind == "runtime_session" else ""),
        "autonomy_level": metadata.get("autonomy_level"),
        "final_state": _final_state(events),
        "last_decision": latest_decision.get("decision") if latest_decision else None,
        "last_successful_action": _last_successful_action(events),
        "counts": counts,
        "top_errors": _build_top_errors(events),
        "key_events": _build_key_events(events),
        "artifacts": artifacts,
        "resume_checkpoint": resume_checkpoint if isinstance(resume_checkpoint, dict) else None,
        "journal_integrity": journal_integrity if isinstance(journal_integrity, dict) else {},
        "command_summary": command_summary,
        "next_actions": [],
        "summary": {
            "mission": metadata.get("mission", command_or_mission if run_kind == "runtime_session" else ""),
            "autonomy_level": metadata.get("autonomy_level", 1),
            "started": started_at,
            "ended": ended_at,
            "duration_seconds": duration_seconds,
            "duration_text": _duration_text(duration_seconds),
            "total_actions": counts["actions"],
            "approvals": counts["approvals"],
            "rejections": counts["rejections"],
            "corrections": counts["corrections"],
            "memory_hits": counts["memory_hits"],
            "api_calls": counts["api_calls"],
            "planner_rejections": counts["planner_rejections"],
            "warnings": counts["warnings"],
            "errors": counts["errors"],
            "captchas": counts["captchas"],
            "timings": counts["timings"],
            "status": status,
            "end_reason": end_reason,
            "final_state": _final_state(events),
            "run_id": run_id,
            "run_kind": run_kind,
        },
        "events": events,
    }
    record["next_actions"] = _default_next_actions(record)
    return record


def render_latest_run_text(record: dict[str, Any]) -> str:
    """Render the fixed-section handoff text for one run record."""

    lines: list[str] = []
    for section in (
        _what_ran_lines(record),
        _outcome_lines(record),
        _resume_boundary_lines(record),
        _key_event_lines(record),
        _error_warning_lines(record),
        _artifact_lines(record),
        _next_action_lines(record),
    ):
        lines.extend(section)
    return "\n".join(lines).rstrip() + "\n"


def _group_test_run_directories(target_dir: Path) -> dict[str, list[Path]]:
    """Group contained pytest run directories by their latest recorded status."""

    grouped: dict[str, list[Path]] = {"success": [], "failed": [], "other": []}
    for path in (candidate for candidate in target_dir.iterdir() if candidate.is_dir()):
        latest_json = path / "latest_run.json"
        payload = _read_json(latest_json) or {}
        status = str(payload.get("status", "other"))
        group_key = status if status in {"success", "failed"} else "other"
        grouped[group_key].append(path)
    return grouped


def _prune_test_run_group(
    paths: list[Path],
    *,
    policy: ArtifactRetentionPolicy,
    current_time: float,
) -> list[Path]:
    """Remove one status bucket of contained pytest runs according to retention policy."""

    cutoff = None if policy.max_age_days is None else current_time - policy.max_age_days * 86400.0
    removed: list[Path] = []
    kept = 0
    for path in sorted(paths, key=lambda candidate: candidate.stat().st_mtime, reverse=True):
        mtime = path.stat().st_mtime
        if kept < policy.max_groups and (cutoff is None or mtime >= cutoff):
            kept += 1
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


class RunRecordSession:
    """Append per-run events and finalize canonical history artifacts."""

    def __init__(
        self,
        *,
        run_kind: str,
        command_or_mission: str,
        run_id: str | None = None,
        output_dir: Path = DEFAULT_SESSION_LOGS_DIR,
        handoff_dir: Path | None = None,
        retention_manager: ArtifactRetentionManager | None = None,
        retention_policy: ArtifactRetentionPolicy | None = None,
        metadata: dict[str, Any] | None = None,
        snapshot_update_interval_seconds: float = DEFAULT_LIVE_SNAPSHOT_INTERVAL_SECONDS,
    ) -> None:
        self.run_kind = str(run_kind)
        self.command_or_mission = str(command_or_mission)
        self.run_id = run_id or build_run_id(self.run_kind)
        self.output_dir = Path(output_dir)
        self.handoff_dir = Path(handoff_dir or DEFAULT_HANDOFF_DIR)
        self.latest_run_json_path = self.handoff_dir / "latest_run.json"
        self.latest_run_text_path = self.handoff_dir / "latest_run.txt"
        self.retention_manager = retention_manager or ArtifactRetentionManager()
        self.retention_policy = retention_policy or DEFAULT_SESSION_RETENTION
        self.metadata = dict(metadata or {})
        self.started_at_dt = _now()
        self.started_at = _isoformat(self.started_at_dt)
        self._start_monotonic = time.monotonic()
        self._terminal_status = "partial"
        self._terminal_reason = "run_in_progress"
        self._terminal_detail = ""
        self._finalized = False
        self._final_record: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._snapshot_update_interval_seconds = max(0.0, float(snapshot_update_interval_seconds))
        self._last_snapshot_monotonic = 0.0
        self.paths = build_run_artifact_paths(self.output_dir, self.run_id)
        with self._lock:
            self._prepare_artifacts()
            self._write_partial_snapshot_locked(force=True)

    def _prepare_artifacts(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.paths.log_path, self.paths.err_path, self.paths.ndjson_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")

    def elapsed_seconds(self) -> float:
        """Return elapsed runtime seconds for the current session."""

        return max(0.0, time.monotonic() - self._start_monotonic)

    def append_output_line(self, stream: str, text: str) -> None:
        """Append raw command output to transcript or stderr files."""

        clean_text = redact_secret(text.rstrip("\n"))
        if not clean_text:
            return
        target = self.paths.err_path if stream == "stderr" else self.paths.log_path
        with self._lock:
            _append_text(target, clean_text + "\n")

    def record_event(
        self,
        event_type: str,
        *,
        detail: str = "",
        severity: str = "INFO",
        update_latest: bool = False,
        **fields: Any,
    ) -> dict[str, Any]:
        """Append one structured event to the per-run event stream."""

        with self._lock:
            if self._finalized:
                return dict(self.events[-1]) if self.events else {}
            event = {
                "run_id": self.run_id,
                "run_kind": self.run_kind,
                "sequence": len(self.events) + 1,
                "timestamp": _isoformat(_now()),
                "elapsed_seconds": round(self.elapsed_seconds(), 1),
                "event_type": str(event_type),
                "detail": redact_secret(detail),
                "severity": str(severity).upper(),
            }
            for key, value in fields.items():
                if _is_blank(value):
                    continue
                event[key] = redact_secret(value) if isinstance(value, str) else value
            self.events.append(event)
            _append_json_line(self.paths.ndjson_path, event)
            _append_text(self.paths.log_path, _event_log_line(event))
            if event["event_type"] == "error" or event["severity"] in {"ERROR", "CRITICAL"}:
                _append_text(self.paths.err_path, _event_log_line(event))
            force_snapshot = bool(update_latest) or event["event_type"] == "terminal" or event["severity"] in {
                "ERROR",
                "CRITICAL",
            }
            self._write_partial_snapshot_locked(force=force_snapshot)
            return dict(event)

    def update_metadata(self, **updates: Any) -> None:
        """Persist metadata that feeds the final handoff payload."""

        updated = False
        with self._lock:
            if self._finalized:
                return
            for key, value in updates.items():
                if _is_blank(value):
                    continue
                self.metadata[key] = value
                updated = True
            if updated:
                self._write_partial_snapshot_locked(force=True)

    def mark_terminal(self, status: str, end_reason: str, detail: str = "") -> None:
        """Record exactly one terminal event for the run."""

        with self._lock:
            if self._finalized:
                return
            if self.events and str(self.events[-1].get("event_type", "")) == "terminal":
                return
            self._terminal_status = _coerce_status(status)
            self._terminal_reason = str(end_reason or "completed")
            self._terminal_detail = detail
            severity = (
                "ERROR"
                if self._terminal_status == "failed"
                else "WARNING"
                if self._terminal_status == "interrupted"
                else "INFO"
            )
            self.record_event(
                "terminal",
                detail=detail or self._terminal_reason,
                severity=severity,
                status=self._terminal_status,
                end_reason=self._terminal_reason,
            )

    def _build_record(self, *, ended_at: str | None = None) -> dict[str, Any]:
        ended_text = ended_at or _isoformat(_now())
        return build_run_record(
            run_id=self.run_id,
            run_kind=self.run_kind,
            command_or_mission=self.command_or_mission,
            started_at=self.started_at,
            ended_at=ended_text,
            status=self._terminal_status,
            end_reason=self._terminal_reason,
            events=list(self.events),
            artifact_paths=self.paths,
            metadata=self.metadata,
        )

    def _write_snapshot_files_locked(self, record: dict[str, Any]) -> None:
        report_text = render_latest_run_text(record)
        atomic_write_text(self.paths.json_path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        atomic_write_text(self.paths.txt_path, report_text)
        atomic_write_text(self.latest_run_json_path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        atomic_write_text(self.latest_run_text_path, report_text)
        self._last_snapshot_monotonic = time.monotonic()

    def _should_refresh_snapshot_locked(self, *, force: bool) -> bool:
        if force:
            return True
        return (time.monotonic() - self._last_snapshot_monotonic) >= self._snapshot_update_interval_seconds

    def _write_partial_snapshot_locked(self, *, force: bool = False) -> None:
        if not self._should_refresh_snapshot_locked(force=force):
            return
        self._write_snapshot_files_locked(self._build_record())

    def finalize(self, *, status: str | None = None, end_reason: str | None = None, detail: str = "") -> Path:
        """Finalize the run exactly once and refresh latest handoff files."""

        with self._lock:
            if self._finalized and self._final_record is not None:
                return self.paths.json_path
            if status or end_reason:
                self.mark_terminal(status or self._terminal_status, end_reason or self._terminal_reason, detail=detail)
            elif not self.events or str(self.events[-1].get("event_type", "")) != "terminal":
                self.mark_terminal("interrupted", "finalized_without_terminal_event", detail=detail)
            self._finalized = True
            ended_at = _isoformat(_now())
            self._final_record = self._build_record(ended_at=ended_at)
            self._write_snapshot_files_locked(self._final_record)
            self.retention_manager.prune_directory(self.output_dir, self.retention_policy)
            return self.paths.json_path

    def summary(self) -> dict[str, Any]:
        """Return the current summary dict for UI/dashboard use."""

        with self._lock:
            return self._build_record()["summary"]

    def timeline(self) -> list[dict[str, Any]]:
        """Return the current event timeline."""

        with self._lock:
            return list(self.events)

    def text_report(self) -> str:
        """Return the fixed-section handoff text for the current run state."""

        with self._lock:
            return render_latest_run_text(self._build_record())


def reconcile_latest_runtime_run(
    *,
    latest_run_path: Path = DEFAULT_LATEST_RUN_JSON,
    handoff_text_path: Path = DEFAULT_LATEST_RUN_TEXT,
) -> dict[str, Any] | None:
    """Finalize an incomplete latest runtime run as interrupted."""

    payload = _read_json(latest_run_path)
    if not payload or str(payload.get("run_kind", "")) != "runtime_session":
        return None
    artifacts = payload.get("artifacts", {})
    event_stream = Path(str(artifacts.get("event_stream", "")))
    if not event_stream.is_file():
        return None
    events = _read_events(event_stream)
    if events and str(events[-1].get("event_type", "")) == "terminal":
        return None
    started_at = str(payload.get("started_at", "") or _isoformat(_now()))
    started_dt = datetime.fromisoformat(started_at)
    ended_at = _isoformat(_now())
    event = {
        "run_id": str(payload.get("run_id", event_stream.stem)),
        "run_kind": "runtime_session",
        "sequence": len(events) + 1,
        "timestamp": ended_at,
        "elapsed_seconds": round(max(0.0, (_now() - started_dt).total_seconds()), 1),
        "event_type": "terminal",
        "detail": "Previous runtime session did not finalize cleanly.",
        "severity": "WARNING",
        "status": "interrupted",
        "end_reason": "previous_run_incomplete",
    }
    _append_json_line(event_stream, event)
    log_path = Path(str(artifacts.get("transcript", "")))
    err_path = Path(str(artifacts.get("stderr", "")))
    if log_path:
        _append_text(log_path, _event_log_line(event))
    if err_path:
        _append_text(err_path, _event_log_line(event))
    base_dir = event_stream.parent
    stem = event_stream.stem
    record = build_run_record(
        run_id=str(payload.get("run_id", stem)),
        run_kind="runtime_session",
        command_or_mission=str(payload.get("command_or_mission", payload.get("mission", ""))),
        started_at=started_at,
        ended_at=ended_at,
        status="interrupted",
        end_reason="previous_run_incomplete",
        events=events + [event],
        artifact_paths=build_run_artifact_paths(base_dir, stem),
        metadata={
            "mission": payload.get("mission", ""),
            "autonomy_level": payload.get("autonomy_level"),
            "runtime_log_path": artifacts.get("runtime_log", DEFAULT_RUNTIME_LOG_PATH),
            "diagnostics_path": artifacts.get("diagnostics", DEFAULT_DIAGNOSTICS_DIR),
            "heartbeat_path": artifacts.get("heartbeat", DEFAULT_HEARTBEAT_PATH),
            "planner_screenshot_path": artifacts.get("planner_screenshot", DEFAULT_PLANNER_SCREENSHOT_PATH),
            "runtime_journal_path": artifacts.get("runtime_journal", ""),
            "runtime_checkpoint_path": artifacts.get("runtime_checkpoint", ""),
            "resume_checkpoint": payload.get("resume_checkpoint", {}),
            "journal_integrity": payload.get("journal_integrity", {}),
            "exit_code": payload.get("exit_code"),
            "command_summary": payload.get("command_summary", {}),
        },
    )
    journal_updates = reconcile_runtime_journal_artifacts(
        run_id=record["run_id"],
        journal_path=Path(str(record["artifacts"].get("runtime_journal", ""))),
        checkpoint_path=Path(str(record["artifacts"].get("runtime_checkpoint", ""))),
        status="interrupted",
        end_reason="previous_run_incomplete",
        detail="Previous runtime session did not finalize cleanly.",
    )
    if journal_updates:
        record = build_run_record(
            run_id=str(payload.get("run_id", stem)),
            run_kind="runtime_session",
            command_or_mission=str(payload.get("command_or_mission", payload.get("mission", ""))),
            started_at=started_at,
            ended_at=ended_at,
            status="interrupted",
            end_reason="previous_run_incomplete",
            events=events + [event],
            artifact_paths=build_run_artifact_paths(base_dir, stem),
            metadata={
                "mission": payload.get("mission", ""),
                "autonomy_level": payload.get("autonomy_level"),
                "runtime_log_path": artifacts.get("runtime_log", DEFAULT_RUNTIME_LOG_PATH),
                "diagnostics_path": artifacts.get("diagnostics", DEFAULT_DIAGNOSTICS_DIR),
                "heartbeat_path": artifacts.get("heartbeat", DEFAULT_HEARTBEAT_PATH),
                "planner_screenshot_path": artifacts.get("planner_screenshot", DEFAULT_PLANNER_SCREENSHOT_PATH),
                "exit_code": payload.get("exit_code"),
                "command_summary": payload.get("command_summary", {}),
                **journal_updates,
            },
        )
    record_path = base_dir / f"{stem}.json"
    text_path = base_dir / f"{stem}.txt"
    atomic_write_text(record_path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    text_report = render_latest_run_text(record)
    atomic_write_text(text_path, text_report)
    atomic_write_text(latest_run_path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(handoff_text_path, text_report)
    LOGGER.warning("Marked incomplete runtime run %s as interrupted.", record["run_id"])
    return record


def prune_test_run_artifacts(
    *,
    base_dir: Path = DEFAULT_TEST_RUNS_DIR,
    now: float | None = None,
    success_policy: ArtifactRetentionPolicy = DEFAULT_TEST_SUCCESS_RETENTION,
    failure_policy: ArtifactRetentionPolicy = DEFAULT_TEST_FAILURE_RETENTION,
) -> list[Path]:
    """Prune contained pytest run directories by status and age."""

    target_dir = Path(base_dir)
    if not target_dir.is_dir():
        return []
    current_time = time.time() if now is None else now
    grouped = _group_test_run_directories(target_dir)

    removed: list[Path] = []
    for status, paths in grouped.items():
        policy = success_policy if status == "success" else failure_policy
        removed.extend(_prune_test_run_group(paths, policy=policy, current_time=current_time))
    return removed


def find_legacy_test_artifacts(project_root: Path = PROJECT_ROOT) -> list[Path]:
    """Return known scattered pytest/temp directories left by old workflows."""

    found: list[Path] = []
    for pattern in _LEGACY_TEST_ARTIFACT_PATTERNS:
        found.extend(path for path in Path(project_root).glob(pattern) if path.exists())
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return sorted(unique)


def cleanup_legacy_test_artifacts(project_root: Path = PROJECT_ROOT) -> list[Path]:
    """Delete legacy scattered pytest/temp directories from old workflows."""

    removed: list[Path] = []
    for path in find_legacy_test_artifacts(project_root):
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=False)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("Unable to remove legacy test artifact %s: %s", path, exc)
            continue
        removed.append(path)
    return removed
