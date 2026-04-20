"""Per-run runtime session logging and handoff generation.

This module is the runtime-facing wrapper over the shared run-handoff contract.
It records planner/runtime events as they happen, keeps a grouped history set
under ``data/session_logs/``, and refreshes ``data/handoff/latest_run.*`` so a
new agent can understand the last run without extra operator context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from artifact_retention import ArtifactRetentionManager, ArtifactRetentionPolicy
from logging_config import get_logger
from run_handoff import (
    DEFAULT_LIVE_SNAPSHOT_INTERVAL_SECONDS,
    DEFAULT_SESSION_LOGS_DIR,
    DEFAULT_SESSION_RETENTION,
    RunRecordSession,
)
from runtime_journal import RuntimeJournal

LOGGER = get_logger(__name__)


@dataclass
class SessionEvent:
    """Compatibility wrapper for older call sites and tests."""

    timestamp: str
    elapsed_seconds: float
    event_type: str
    action_type: str = ""
    label: str = ""
    target_id: str = ""
    outcome: str = ""
    detail: str = ""
    stage: str = ""
    duration_ms: float | None = None
    severity: str = "INFO"
    source: str = ""
    state_text: str = ""
    decision: dict[str, Any] | None = None
    status: str = ""
    end_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "timestamp": self.timestamp,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "event_type": self.event_type,
            "action_type": self.action_type,
            "label": self.label,
            "target_id": self.target_id,
            "outcome": self.outcome,
            "detail": self.detail,
            "severity": self.severity,
            "source": self.source,
        }
        if self.stage:
            payload["stage"] = self.stage
        if self.duration_ms is not None:
            payload["duration_ms"] = round(self.duration_ms, 2)
        if self.state_text:
            payload["state_text"] = self.state_text
        if self.decision is not None:
            payload["decision"] = self.decision
        if self.status:
            payload["status"] = self.status
        if self.end_reason:
            payload["end_reason"] = self.end_reason
        return payload


class SessionLogger:
    """Record and finalize one runtime automation session.

    Args:
        mission: Operator-selected mission text for the run.
        autonomy_level: Current autonomy level selected in the UI.
        output_dir: Directory used for grouped per-run history files.
        retention_manager: Optional grouped-retention helper.
        retention_policy: Optional history retention policy override.
    """

    def __init__(
        self,
        mission: str = "",
        autonomy_level: int = 1,
        output_dir: Path = DEFAULT_SESSION_LOGS_DIR,
        handoff_dir: Path | None = None,
        retention_manager: ArtifactRetentionManager | None = None,
        retention_policy: ArtifactRetentionPolicy | None = None,
        snapshot_update_interval_seconds: float | None = None,
    ) -> None:
        self.mission = str(mission)
        self.autonomy_level = int(autonomy_level)
        self._session = RunRecordSession(
            run_kind="runtime_session",
            command_or_mission=self.mission,
            output_dir=Path(output_dir),
            handoff_dir=handoff_dir,
            retention_manager=retention_manager,
            retention_policy=retention_policy or DEFAULT_SESSION_RETENTION,
            metadata={
                "mission": self.mission,
                "autonomy_level": self.autonomy_level,
            },
            snapshot_update_interval_seconds=(
                snapshot_update_interval_seconds
                if snapshot_update_interval_seconds is not None
                else DEFAULT_LIVE_SNAPSHOT_INTERVAL_SECONDS
            ),
        )
        self._runtime_journal = RuntimeJournal(
            run_id=self.run_id,
            output_dir=Path(output_dir),
        )
        self._refresh_runtime_journal_metadata()

    @property
    def run_id(self) -> str:
        """Return the stable run identifier for this session."""

        return self._session.run_id

    @property
    def paths(self):
        """Expose grouped output paths for callers that need them."""

        return self._session.paths

    def log_context_fields(self) -> dict[str, Any]:
        """Return correlation fields suitable for structured log binding."""

        return {
            "run_id": self.run_id,
            "session_id": self.run_id,
            "run_kind": self._session.run_kind,
        }

    def _record(self, event_type: str, *, detail: str = "", severity: str = "INFO", **fields: Any) -> None:
        self._session.record_event(event_type, detail=detail, severity=severity, **fields)

    def _refresh_runtime_journal_metadata(self) -> None:
        self._session.update_metadata(**self._runtime_journal.metadata())

    def update_metadata(self, **updates: Any) -> None:
        """Merge metadata that should appear in the final handoff payload."""

        self._session.update_metadata(**updates)

    def record_action(
        self,
        action_type: str,
        label: str = "",
        target_id: str = "",
        outcome: str = "success",
        source: str = "ai",
    ) -> None:
        """Record one guarded planner action execution."""

        self._record(
            "action",
            action_type=action_type,
            label=label,
            target_id=target_id,
            outcome=outcome,
            source=source,
        )

    def record_approval(self, label: str = "") -> None:
        """Record a user approval event."""

        self._record("approval", label=label, outcome="approved")

    def record_rejection(self, label: str = "", detail: str = "") -> None:
        """Record a user rejection event."""

        self._record("rejection", label=label, outcome="rejected", detail=detail, severity="WARNING")

    def record_correction(self, label: str = "") -> None:
        """Record a manual correction event."""

        self._record("correction", label=label, outcome="corrected")

    def record_error(
        self,
        detail: str = "",
        *,
        stage: str = "",
        action_type: str = "",
        label: str = "",
        target_id: str = "",
        outcome: str = "error",
    ) -> None:
        """Record a runtime error at the real failing boundary."""

        self._record(
            "error",
            detail=detail,
            severity="ERROR",
            stage=stage,
            action_type=action_type,
            label=label,
            target_id=target_id,
            outcome=outcome,
        )

    def record_warning(self, detail: str, *, stage: str = "", label: str = "") -> None:
        """Record a warning that should show up in the handoff payload."""

        self._record("warning", detail=detail, severity="WARNING", stage=stage, label=label)

    def record_planner_rejection(
        self,
        reason: str = "",
        action_type: str = "",
        label: str = "",
        target_id: str = "",
        confidence: Any = "",
    ) -> None:
        """Record a planner proposal rejected before guarded input."""

        detail = f"reason={reason}"
        if confidence is not None and confidence != "":
            detail = f"{detail} confidence={confidence}"
        self._record(
            "planner_rejection",
            detail=detail,
            severity="WARNING",
            action_type=action_type,
            label=label,
            target_id=target_id,
            outcome="rejected",
        )

    def record_captcha(self) -> None:
        """Record a CAPTCHA pause event."""

        self._record("captcha", detail="CAPTCHA detected; waiting for manual review.", severity="WARNING", outcome="paused")

    def record_info(self, detail: str) -> None:
        """Record an informational runtime note."""

        self._record("info", detail=detail)

    def record_state(self, state_text: str) -> None:
        """Record the latest operator-facing runtime state."""

        self._record("state", detail=state_text, state_text=state_text)

    def record_decision(self, decision: dict[str, Any]) -> None:
        """Record the latest planner decision summary."""

        self._record(
            "decision",
            detail=str(decision.get("reason", "") or decision.get("label", "") or decision.get("action_type", "")),
            action_type=str(decision.get("action_type", "") or ""),
            label=str(decision.get("label", "") or ""),
            target_id=str(decision.get("target_id", "") or ""),
            decision=decision,
        )

    def record_step_started(self, *, machine_id: str, state_name: str, action_name: str) -> str:
        """Record the start of one logical workflow step."""

        return self._runtime_journal.record_step_started(
            machine_id=machine_id,
            state_name=state_name,
            action_name=action_name,
        )

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
        """Record that a logical step ended without committing a new state."""

        self._runtime_journal.record_step_aborted(
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
        """Record the planner decision attached to the active logical step."""

        return self._runtime_journal.record_decision_selected(
            step_id=step_id,
            machine_id=machine_id,
            state_name=state_name,
            action_type=action_type,
            label=label,
            target_id=target_id,
            source=source,
            confidence=confidence,
        )

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
        """Record that the runtime is waiting for a human approval decision."""

        return self._runtime_journal.record_approval_requested(
            step_id=step_id,
            machine_id=machine_id,
            state_name=state_name,
            decision_id=decision_id,
            action_type=action_type,
            label=label,
            target_id=target_id,
            fix_required=fix_required,
        )

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
        """Record the resolved outcome for a pending approval boundary."""

        self._runtime_journal.record_approval_resolved(
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
        """Record the start of one guarded hardware-input dispatch."""

        return self._runtime_journal.record_input_started(
            step_id=step_id,
            machine_id=machine_id,
            state_name=state_name,
            decision_id=decision_id,
            action_type=action_type,
            label=label,
            target_id=target_id,
        )

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
        """Record the result of one guarded hardware-input dispatch."""

        self._runtime_journal.record_input_completed(
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

        self._runtime_journal.record_transition_committed(
            step_id=step_id,
            machine_id=machine_id,
            state_name=state_name,
            action_name=action_name,
            event=event,
            result=result,
            next_state=next_state,
            decision_id=decision_id,
            action_type=action_type,
            label=label,
            target_id=target_id,
        )
        self._refresh_runtime_journal_metadata()

    def record_timing(self, stage: str, duration_ms: float, detail: str = "") -> None:
        """Record one bounded runtime timing measurement."""

        self._record(
            "timing",
            detail=str(detail),
            stage=str(stage),
            duration_ms=max(0.0, float(duration_ms)),
            outcome="observed",
        )

    def mark_terminal(self, status: str, end_reason: str, detail: str = "", *, machine_id: str = "") -> None:
        """Record exactly one terminal event for the session."""

        self._runtime_journal.record_terminal(
            status=status,
            end_reason=end_reason,
            detail=detail,
            machine_id=machine_id,
        )
        self._refresh_runtime_journal_metadata()
        self._session.mark_terminal(status, end_reason, detail=detail)

    def duration_seconds(self) -> float:
        """Return elapsed session seconds."""

        return self._session.elapsed_seconds()

    def duration_text(self) -> str:
        """Return a human-readable duration string."""

        return str(self.summary().get("duration_text", "0s"))

    def summary(self) -> dict[str, Any]:
        """Return the current summary payload."""

        return self._session.summary()

    def timeline(self) -> list[dict[str, Any]]:
        """Return the current event stream as plain dictionaries."""

        return self._session.timeline()

    def text_report(self) -> str:
        """Return the fixed-section latest-run handoff text."""

        return self._session.text_report()

    def finalize(self, *, status: str | None = None, end_reason: str | None = None, detail: str = "") -> Path:
        """Finalize the runtime session exactly once and return the JSON path."""

        summary = self._session.summary()
        if status or end_reason:
            self.mark_terminal(status or str(summary.get("status", "partial")), end_reason or str(summary.get("end_reason", "completed")), detail=detail)
        elif str(summary.get("status", "partial") or "partial") == "partial":
            self.mark_terminal("interrupted", "finalized_without_terminal_event", detail=detail)
        path = self._session.finalize(detail=detail)
        LOGGER.info("Session log saved: %s", path)
        return path
