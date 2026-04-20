"""Durable append-only runtime journal for resumable workflow boundaries.

This module owns the crash-recovery journal used by runtime sessions. It keeps
an HMAC-chained NDJSON event stream plus an atomic checkpoint file that points
to the last committed logical transition. The checkpoint is the supported
resume boundary after crashes or emergency termination: operators must
re-observe the screen before allowing any new input, and the bot must not
assume uncommitted tail events completed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config_manager import ConfigManager
from logging_config import get_logger
from security_utils import atomic_write_text, redact_secret

LOGGER = get_logger(__name__)

JOURNAL_SCHEMA_VERSION = 1
JOURNAL_SECRET_KEY_NAME = "RUNTIME_JOURNAL_HMAC_KEY"
RESUME_POLICY = "reobserve_before_input"
_RESUME_COMMITTED_FIELDS = (
    ("last_committed_at", "timestamp"),
    ("machine_id", "machine_id"),
    ("step_id", "step_id"),
    ("state_name", "state_name"),
    ("next_state", "next_state"),
    ("event", "event"),
    ("action_name", "action_name"),
    ("action_type", "action_type"),
    ("label", "label"),
    ("target_id", "target_id"),
    ("decision_id", "decision_id"),
)


def _now() -> datetime:
    """Return the current wall-clock timestamp for journal artifacts."""

    return datetime.now()


def _isoformat(value: datetime) -> str:
    """Serialize journal timestamps using second precision."""

    return value.isoformat(timespec="seconds")


def _normalize_secret_key(secret_key: str) -> bytes:
    """Normalize operator-provided journal secret text into raw bytes."""

    text = str(secret_key).strip()
    if not text:
        raise ValueError("Runtime journal secret key must not be empty.")
    try:
        decoded = bytes.fromhex(text)
    except ValueError:
        decoded = text.encode("utf-8")
    if not decoded:
        raise ValueError("Runtime journal secret key must not decode to empty bytes.")
    return decoded


def _resolve_secret_bytes(
    *,
    secret_key: str | None = None,
    config: ConfigManager | None = None,
    create_if_missing: bool,
) -> bytes | None:
    """Resolve the HMAC key from explicit input or configured secret storage."""

    if secret_key not in {None, ""}:
        return _normalize_secret_key(str(secret_key))

    manager = config or ConfigManager()
    existing = manager.get(JOURNAL_SECRET_KEY_NAME, "")
    if existing not in {None, ""}:
        return _normalize_secret_key(str(existing))
    if not create_if_missing:
        return None

    generated = secrets.token_hex(32)
    try:
        manager.set_many({JOURNAL_SECRET_KEY_NAME: generated})
    except Exception as exc:
        LOGGER.warning("Unable to persist runtime journal HMAC key; using an in-memory fallback: %s", exc)
    return _normalize_secret_key(generated)


def _append_json_line(path: Path, payload: dict[str, Any]) -> None:
    """Append one runtime-journal event as NDJSON with fsync semantics."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> dict[str, Any] | None:
    """Read one JSON artifact, returning `None` for missing or invalid files."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class RuntimeJournalPaths:
    """Grouped per-run journal artifacts."""

    directory: Path
    journal_path: Path
    checkpoint_path: Path


def build_runtime_journal_paths(output_dir: Path, run_id: str) -> RuntimeJournalPaths:
    """Return the journal and checkpoint paths for one runtime session."""

    base_dir = Path(output_dir)
    return RuntimeJournalPaths(
        directory=base_dir,
        journal_path=base_dir / f"{run_id}.journal.ndjson",
        checkpoint_path=base_dir / f"{run_id}.checkpoint.json",
    )


def _canonical_payload(payload: dict[str, Any]) -> str:
    """Return the canonical serialized payload used for journal HMACs."""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _compute_entry_hmac(secret: bytes, previous_hmac: str, payload: dict[str, Any]) -> str:
    """Compute the chained HMAC for one journal payload."""

    message = f"{previous_hmac}\n{_canonical_payload(payload)}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _coerce_bool(value: Any) -> bool | None:
    """Return boolean values unchanged and reject non-boolean inputs."""

    if isinstance(value, bool):
        return value
    return None


def _base_event_payload(*, run_id: str, sequence: int, event_type: str) -> dict[str, Any]:
    """Build the common payload fields shared by all journal entries."""

    return {
        "version": JOURNAL_SCHEMA_VERSION,
        "run_id": run_id,
        "sequence": sequence,
        "timestamp": _isoformat(_now()),
        "event_type": str(event_type),
    }


def _sanitized_event_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Return event fields with empty values removed and secrets redacted."""

    payload: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None or value == "":
            continue
        payload[key] = redact_secret(value) if isinstance(value, str) else value
    return payload


def _signed_entry(*, secret: bytes, previous_hmac: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return one journal entry with chained HMAC bookkeeping fields added."""

    entry = dict(payload)
    entry["previous_hmac"] = previous_hmac
    entry["entry_hmac"] = _compute_entry_hmac(secret, previous_hmac, payload)
    return entry


def _should_refresh_checkpoint(*, event_type: str, commit_boundary: bool) -> bool:
    """Return whether a journal append should also refresh the checkpoint."""

    return commit_boundary or event_type == "terminal"


def _tail_event_types(entries: list[dict[str, Any]], committed_sequence: int) -> list[str]:
    """Return uncommitted non-terminal event types after the last durable checkpoint."""

    event_types: list[str] = []
    for entry in entries:
        sequence = int(entry.get("sequence", 0) or 0)
        if sequence <= committed_sequence:
            continue
        event_type = str(entry.get("event_type", "") or "")
        if not event_type or event_type == "terminal":
            continue
        event_types.append(event_type)
    return event_types


def _last_event(entries: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    """Return the newest event of a given type from the journal stream."""

    for entry in reversed(entries):
        if str(entry.get("event_type", "")) == event_type:
            return entry
    return None


def _entry_text(entry: dict[str, Any] | None, key: str) -> str:
    """Return one journal-entry field as normalized text."""

    return str(entry.get(key, "") or "") if entry else ""


def _resume_committed_fields(last_committed: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the last committed transition fields used by resume metadata."""

    payload = {output_key: _entry_text(last_committed, source_key) for output_key, source_key in _RESUME_COMMITTED_FIELDS}
    payload["result"] = _coerce_bool(last_committed.get("result")) if last_committed else None
    return payload


def _resume_summary_from_entries(
    *,
    run_id: str,
    entries: list[dict[str, Any]],
    verified: bool,
    terminal_event: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the resume summary payload from verified journal entries."""

    last_committed = _last_event(entries, "transition_committed")
    committed_sequence = int(last_committed.get("sequence", 0) or 0) if last_committed else 0
    pending_tail_events = _tail_event_types(entries, committed_sequence)
    payload = {
        "run_id": run_id,
        "verified": bool(verified),
        "resume_policy": RESUME_POLICY,
        "last_sequence": int(entries[-1].get("sequence", 0) or 0) if entries else 0,
        "last_committed_sequence": committed_sequence,
        "pending_tail_count": len(pending_tail_events),
        "pending_tail_events": pending_tail_events[-8:],
        "terminal_status": _entry_text(terminal_event, "status"),
        "terminal_reason": _entry_text(terminal_event, "end_reason"),
    }
    payload.update(_resume_committed_fields(last_committed))
    return payload


def _journal_integrity_payload(
    entries: list[dict[str, Any]],
    *,
    verified: bool,
    last_committed: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the integrity subsection for one checkpoint payload."""

    last_entry = entries[-1] if entries else {}
    return {
        "verified": bool(verified),
        "algorithm": "hmac-sha256",
        "last_sequence": int(last_entry.get("sequence", 0) or 0),
        "last_entry_hmac": str(last_entry.get("entry_hmac", "") or ""),
        "last_committed_sequence": int(last_committed.get("sequence", 0) or 0) if last_committed else 0,
        "last_committed_entry_hmac": str(last_committed.get("entry_hmac", "") or "") if last_committed else "",
    }


def _terminal_payload(terminal_event: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build the terminal subsection for one checkpoint payload."""

    if terminal_event is None:
        return None
    return {
        "timestamp": _entry_text(terminal_event, "timestamp"),
        "status": _entry_text(terminal_event, "status"),
        "end_reason": _entry_text(terminal_event, "end_reason"),
        "detail": _entry_text(terminal_event, "detail"),
    }


def _terminal_event_payload(
    *,
    run_id: str,
    sequence: int,
    status: str,
    end_reason: str,
    detail: str,
) -> dict[str, Any]:
    """Build the payload used when reconciling a missing terminal event."""

    payload = _base_event_payload(run_id=run_id, sequence=sequence, event_type="terminal")
    payload.update(
        _sanitized_event_fields(
            {
                "status": str(status),
                "end_reason": str(end_reason),
                "detail": detail or end_reason,
            }
        )
    )
    return payload


def _reconcile_checkpoint_payload(run_id: str, checkpoint_path: Path) -> dict[str, Any]:
    """Return the best-effort checkpoint payload when the journal secret is unavailable."""

    return _read_json(checkpoint_path) or {
        "resume_checkpoint": {
            "run_id": run_id,
            "verified": False,
            "resume_policy": RESUME_POLICY,
            "pending_tail_count": 0,
            "pending_tail_events": [],
        },
        "journal_integrity": {
            "verified": False,
            "algorithm": "hmac-sha256",
            "last_sequence": 0,
            "last_entry_hmac": "",
            "last_committed_sequence": 0,
            "last_committed_entry_hmac": "",
        },
    }


def _journal_metadata(
    *,
    journal_path: Path,
    checkpoint_path: Path,
    checkpoint_payload: dict[str, Any],
) -> dict[str, Any]:
    """Return the handoff metadata emitted after journal reconciliation."""

    return {
        "runtime_journal_path": str(journal_path),
        "runtime_checkpoint_path": str(checkpoint_path),
        "resume_checkpoint": checkpoint_payload.get("resume_checkpoint", {}),
        "journal_integrity": checkpoint_payload.get("journal_integrity", {}),
    }


def _should_append_terminal_event(
    *,
    verified: bool,
    status: str,
    terminal_event: dict[str, Any] | None,
) -> bool:
    """Return whether reconciliation should append a terminal journal marker."""

    return verified and bool(status) and status != "partial" and terminal_event is None


def _checkpoint_payload(
    *,
    run_id: str,
    entries: list[dict[str, Any]],
    verified: bool,
    terminal_event: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the durable checkpoint document for the current journal state."""

    last_committed = _last_event(entries, "transition_committed")
    return {
        "version": JOURNAL_SCHEMA_VERSION,
        "run_id": run_id,
        "updated_at": _isoformat(_now()),
        "resume_policy": RESUME_POLICY,
        "journal_integrity": _journal_integrity_payload(entries, verified=verified, last_committed=last_committed),
        "resume_checkpoint": _resume_summary_from_entries(
            run_id=run_id,
            entries=entries,
            verified=verified,
            terminal_event=terminal_event,
        ),
        "terminal": _terminal_payload(terminal_event),
    }


def _entry_payload_for_hmac(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the journal entry payload with HMAC bookkeeping fields removed."""

    return {key: value for key, value in entry.items() if key not in {"previous_hmac", "entry_hmac"}}


def _validated_journal_entry(
    raw_line: str,
    *,
    previous_hmac: str,
    secret: bytes,
) -> tuple[dict[str, Any] | None, str]:
    """Parse and validate one raw journal line against the chained HMAC."""

    try:
        entry = json.loads(raw_line)
    except json.JSONDecodeError:
        return None, previous_hmac
    if not isinstance(entry, dict):
        return None, previous_hmac

    expected_previous_hmac = str(entry.get("previous_hmac", "") or "")
    expected_entry_hmac = str(entry.get("entry_hmac", "") or "")
    computed_entry_hmac = _compute_entry_hmac(secret, previous_hmac, _entry_payload_for_hmac(entry))
    if expected_previous_hmac != previous_hmac or expected_entry_hmac != computed_entry_hmac:
        return None, previous_hmac
    return entry, expected_entry_hmac


def _verified_entries(journal_path: Path, secret: bytes) -> tuple[list[dict[str, Any]], bool]:
    """Read journal entries until the first decode or HMAC validation failure."""

    entries: list[dict[str, Any]] = []
    previous_hmac = ""

    if not journal_path.is_file():
        return entries, True

    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        LOGGER.warning("Unable to read runtime journal %s: %s", journal_path, exc)
        return entries, False

    for raw_line in lines:
        if not raw_line.strip():
            continue
        entry, next_hmac = _validated_journal_entry(raw_line, previous_hmac=previous_hmac, secret=secret)
        if entry is None:
            return entries, False
        entries.append(entry)
        previous_hmac = next_hmac
    return entries, True


class RuntimeJournal:
    """Append HMAC-chained runtime events and maintain a resume checkpoint.

    The checkpoint tracks the last committed logical transition, not the last
    low-level input event. That distinction is intentional: after a crash or
    F12 emergency stop, OSROKBOT resumes from the last committed state and
    re-observes the screen before permitting any new input.
    """

    def __init__(
        self,
        *,
        run_id: str,
        output_dir: Path,
        config: ConfigManager | None = None,
        secret_key: str | None = None,
    ) -> None:
        self.run_id = str(run_id)
        self.paths = build_runtime_journal_paths(output_dir, self.run_id)
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._previous_hmac = ""
        self._terminal_recorded = False
        self._secret = _resolve_secret_bytes(
            secret_key=secret_key,
            config=config,
            create_if_missing=True,
        )
        if self._secret is None:
            raise RuntimeError("Runtime journal secret key could not be resolved.")
        self.paths.directory.mkdir(parents=True, exist_ok=True)
        if not self.paths.journal_path.exists():
            self.paths.journal_path.write_text("", encoding="utf-8")
        self._write_checkpoint_locked(verified=True)

    def _next_identifier_locked(self, prefix: str) -> str:
        return f"{prefix}_{len(self._entries) + 1:06d}"

    def _record_entry_locked(self, entry: dict[str, Any]) -> None:
        """Store one signed entry in memory and update terminal bookkeeping."""

        self._entries.append(entry)
        self._previous_hmac = str(entry["entry_hmac"])
        if str(entry.get("event_type", "")) == "terminal":
            self._terminal_recorded = True

    def _append_event_locked(self, event_type: str, *, commit_boundary: bool = False, **fields: Any) -> dict[str, Any]:
        if self._terminal_recorded and event_type != "terminal":
            return dict(self._entries[-1]) if self._entries else {}

        payload = _base_event_payload(
            run_id=self.run_id,
            sequence=len(self._entries) + 1,
            event_type=event_type,
        )
        payload.update(_sanitized_event_fields(fields))
        entry = _signed_entry(secret=self._secret, previous_hmac=self._previous_hmac, payload=payload)
        _append_json_line(self.paths.journal_path, entry)
        self._record_entry_locked(entry)
        if _should_refresh_checkpoint(event_type=event_type, commit_boundary=commit_boundary):
            self._write_checkpoint_locked(verified=True)
        return dict(entry)

    def _write_checkpoint_locked(self, *, verified: bool) -> None:
        terminal_event = _last_event(self._entries, "terminal")
        payload = _checkpoint_payload(
            run_id=self.run_id,
            entries=list(self._entries),
            verified=verified,
            terminal_event=terminal_event,
        )
        atomic_write_text(
            self.paths.checkpoint_path,
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )

    def metadata(self) -> dict[str, Any]:
        """Return handoff metadata describing the current journal state."""

        with self._lock:
            checkpoint = _checkpoint_payload(
                run_id=self.run_id,
                entries=list(self._entries),
                verified=True,
                terminal_event=_last_event(self._entries, "terminal"),
            )
            return {
                "runtime_journal_path": str(self.paths.journal_path),
                "runtime_checkpoint_path": str(self.paths.checkpoint_path),
                "resume_checkpoint": checkpoint["resume_checkpoint"],
                "journal_integrity": checkpoint["journal_integrity"],
            }

    def record_step_started(self, *, machine_id: str, state_name: str, action_name: str) -> str:
        """Append the start of one logical workflow step and return its ID."""

        with self._lock:
            step_id = self._next_identifier_locked("step")
            self._append_event_locked(
                "step_started",
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                action_name=action_name,
            )
            return step_id

    def record_step_aborted(
        self,
        *,
        step_id: str,
        machine_id: str,
        state_name: str,
        action_name: str,
        reason: str,
        detail: str = "",
    ) -> None:
        """Append an uncommitted step abort without changing the resume boundary."""

        with self._lock:
            self._append_event_locked(
                "step_aborted",
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                action_name=action_name,
                reason=reason,
                detail=detail,
            )

    def record_decision_selected(
        self,
        *,
        step_id: str,
        machine_id: str,
        state_name: str,
        action_type: str,
        label: str = "",
        target_id: str = "",
        source: str = "",
        confidence: float | None = None,
    ) -> str:
        """Append a planner decision selection event and return its ID."""

        with self._lock:
            decision_id = self._next_identifier_locked("decision")
            self._append_event_locked(
                "decision_selected",
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                decision_id=decision_id,
                action_type=action_type,
                label=label,
                target_id=target_id,
                source=source,
                confidence=confidence,
            )
            return decision_id

    def record_approval_requested(
        self,
        *,
        step_id: str,
        machine_id: str,
        state_name: str,
        decision_id: str,
        action_type: str,
        label: str = "",
        target_id: str = "",
        fix_required: bool = False,
    ) -> str:
        """Append a human-approval wait boundary and return its approval ID."""

        with self._lock:
            approval_id = self._next_identifier_locked("approval")
            self._append_event_locked(
                "approval_requested",
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                decision_id=decision_id,
                approval_id=approval_id,
                action_type=action_type,
                label=label,
                target_id=target_id,
                fix_required=bool(fix_required),
            )
            return approval_id

    def record_approval_resolved(
        self,
        *,
        step_id: str,
        machine_id: str,
        state_name: str,
        decision_id: str,
        approval_id: str,
        outcome: str,
        corrected_point: dict[str, float] | None = None,
    ) -> None:
        """Append the result of a pending human approval boundary."""

        with self._lock:
            self._append_event_locked(
                "approval_resolved",
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                decision_id=decision_id,
                approval_id=approval_id,
                outcome=outcome,
                corrected_point=corrected_point,
            )

    def record_input_started(
        self,
        *,
        step_id: str,
        machine_id: str,
        state_name: str,
        decision_id: str,
        action_type: str,
        label: str = "",
        target_id: str = "",
    ) -> str:
        """Append the start of one guarded hardware-input dispatch."""

        with self._lock:
            input_id = self._next_identifier_locked("input")
            self._append_event_locked(
                "input_started",
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                decision_id=decision_id,
                input_id=input_id,
                action_type=action_type,
                label=label,
                target_id=target_id,
            )
            return input_id

    def record_input_completed(
        self,
        *,
        step_id: str,
        machine_id: str,
        state_name: str,
        decision_id: str,
        input_id: str,
        action_type: str,
        outcome: str,
        label: str = "",
        target_id: str = "",
        detail: str = "",
    ) -> None:
        """Append the result of one guarded hardware-input dispatch."""

        with self._lock:
            self._append_event_locked(
                "input_completed",
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                decision_id=decision_id,
                input_id=input_id,
                action_type=action_type,
                outcome=outcome,
                label=label,
                target_id=target_id,
                detail=detail,
            )

    def record_transition_committed(
        self,
        *,
        step_id: str,
        machine_id: str,
        state_name: str,
        action_name: str,
        event: str,
        result: bool,
        next_state: str | None,
        decision_id: str = "",
        action_type: str = "",
        label: str = "",
        target_id: str = "",
    ) -> None:
        """Advance the durable resume boundary to one committed transition."""

        with self._lock:
            self._append_event_locked(
                "transition_committed",
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                action_name=action_name,
                event=event,
                result=bool(result),
                next_state=next_state or state_name,
                decision_id=decision_id,
                action_type=action_type,
                label=label,
                target_id=target_id,
                commit_boundary=True,
            )

    def record_terminal(self, *, status: str, end_reason: str, detail: str = "", machine_id: str = "") -> None:
        """Append a terminal runtime event and refresh the checkpoint."""

        with self._lock:
            if self._terminal_recorded:
                return
            self._append_event_locked(
                "terminal",
                status=status,
                end_reason=end_reason,
                detail=detail,
                machine_id=machine_id,
            )


def reconcile_runtime_journal_artifacts(
    *,
    run_id: str,
    journal_path: Path,
    checkpoint_path: Path,
    status: str,
    end_reason: str,
    detail: str = "",
    config: ConfigManager | None = None,
    secret_key: str | None = None,
) -> dict[str, Any]:
    """Verify a runtime journal, append a terminal marker when needed, and rebuild its checkpoint."""

    journal_path = Path(journal_path)
    checkpoint_path = Path(checkpoint_path)
    if not journal_path.is_file() and not checkpoint_path.is_file():
        return {}

    secret = _resolve_secret_bytes(
        secret_key=secret_key,
        config=config,
        create_if_missing=False,
    )
    if secret is None:
        checkpoint_payload = _reconcile_checkpoint_payload(run_id, checkpoint_path)
        return _journal_metadata(
            journal_path=journal_path,
            checkpoint_path=checkpoint_path,
            checkpoint_payload=checkpoint_payload,
        )

    entries, verified = _verified_entries(journal_path, secret)
    terminal_event = _last_event(entries, "terminal")
    if _should_append_terminal_event(verified=verified, status=status, terminal_event=terminal_event):
        previous_hmac = str(entries[-1].get("entry_hmac", "") or "") if entries else ""
        payload = _terminal_event_payload(
            run_id=run_id,
            sequence=len(entries) + 1,
            status=status,
            end_reason=end_reason,
            detail=detail,
        )
        entry = _signed_entry(secret=secret, previous_hmac=previous_hmac, payload=payload)
        _append_json_line(journal_path, entry)
        entries.append(entry)
        terminal_event = entry

    checkpoint_payload = _checkpoint_payload(
        run_id=run_id,
        entries=entries,
        verified=verified,
        terminal_event=terminal_event,
    )
    atomic_write_text(
        checkpoint_path,
        json.dumps(checkpoint_payload, indent=2, ensure_ascii=False) + "\n",
    )
    return _journal_metadata(
        journal_path=journal_path,
        checkpoint_path=checkpoint_path,
        checkpoint_payload=checkpoint_payload,
    )
