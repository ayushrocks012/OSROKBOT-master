"""Per-run session logging and summary generation.

Records events (actions, approvals, errors) during an automation run and
generates a structured JSON summary in ``data/sessions/`` when the run stops.
"""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from artifact_retention import ArtifactRetentionManager, ArtifactRetentionPolicy, policy_from_environment
from logging_config import get_logger

LOGGER = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SESSIONS_DIR = PROJECT_ROOT / "data" / "session_logs"
DEFAULT_SESSION_RETENTION = policy_from_environment(
    max_groups_env="ROK_SESSION_LOG_MAX_FILES",
    max_age_days_env="ROK_SESSION_LOG_MAX_AGE_DAYS",
    default_max_groups=200,
    default_max_age_days=30.0,
)


@dataclass
class SessionEvent:
    """One recorded event during an automation session."""

    timestamp: str
    elapsed_seconds: float
    event_type: str  # action, approval, rejection, correction, error, captcha, info
    action_type: str = ""
    label: str = ""
    target_id: str = ""
    outcome: str = ""  # success, failure, approved, rejected, corrected
    detail: str = ""
    stage: str = ""
    duration_ms: float | None = None

    def to_dict(self):
        payload = {
            "timestamp": self.timestamp,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "event_type": self.event_type,
            "action_type": self.action_type,
            "label": self.label,
            "target_id": self.target_id,
            "outcome": self.outcome,
            "detail": self.detail,
        }
        if self.stage:
            payload["stage"] = self.stage
        if self.duration_ms is not None:
            payload["duration_ms"] = round(self.duration_ms, 2)
        return payload


class SessionLogger:
    """Records and summarizes a single automation session.

    Usage::

        logger = SessionLogger(mission="Farm resources")
        logger.record_action("click", "gather_button", "det_3", "success")
        logger.record_approval("gather_button")
        # ... at end of run ...
        logger.finalize()
        summary = logger.summary()
    """

    def __init__(
        self,
        mission="",
        autonomy_level=1,
        output_dir=DEFAULT_SESSIONS_DIR,
        retention_manager: ArtifactRetentionManager | None = None,
        retention_policy: ArtifactRetentionPolicy | None = None,
    ):
        self.mission = str(mission)
        self.autonomy_level = int(autonomy_level)
        self.output_dir = Path(output_dir)
        self.retention_manager = retention_manager or ArtifactRetentionManager()
        self.retention_policy = retention_policy or DEFAULT_SESSION_RETENTION
        self.events: list[SessionEvent] = []
        self._start_time = time.monotonic()
        self._start_datetime = datetime.now()
        self._end_datetime = None

        # Counters.
        self.action_count = 0
        self.approval_count = 0
        self.rejection_count = 0
        self.correction_count = 0
        self.memory_hit_count = 0
        self.api_call_count = 0
        self.planner_rejection_count = 0
        self.error_count = 0
        self.captcha_count = 0
        self.timing_count = 0

    def _elapsed(self):
        return time.monotonic() - self._start_time

    def _now_iso(self):
        return datetime.now().isoformat(timespec="seconds")

    def record_action(self, action_type, label="", target_id="", outcome="success", source="ai"):
        """Record a planner action execution."""
        self.action_count += 1
        if source == "memory":
            self.memory_hit_count += 1
        else:
            self.api_call_count += 1
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="action",
            action_type=action_type,
            label=label,
            target_id=target_id,
            outcome=outcome,
        ))

    def record_approval(self, label=""):
        """Record a user approval."""
        self.approval_count += 1
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="approval",
            label=label,
            outcome="approved",
        ))

    def record_rejection(self, label=""):
        """Record a user rejection."""
        self.rejection_count += 1
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="rejection",
            label=label,
            outcome="rejected",
        ))

    def record_correction(self, label=""):
        """Record a user correction (Fix)."""
        self.correction_count += 1
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="correction",
            label=label,
            outcome="corrected",
        ))

    def record_error(self, detail=""):
        """Record an error."""
        self.error_count += 1
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="error",
            outcome="error",
            detail=detail,
        ))

    def record_planner_rejection(self, reason="", action_type="", label="", target_id="", confidence=""):
        """Record a planner proposal rejected before guarded input."""
        self.planner_rejection_count += 1
        detail = f"reason={reason}"
        if confidence != "":
            detail = f"{detail} confidence={confidence}"
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="planner_rejection",
            action_type=action_type,
            label=label,
            target_id=target_id,
            outcome="rejected",
            detail=detail,
        ))

    def record_captcha(self):
        """Record a CAPTCHA detection."""
        self.captcha_count += 1
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="captcha",
            outcome="paused",
        ))

    def record_info(self, detail):
        """Record a general info event."""
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="info",
            detail=detail,
        ))

    def record_timing(self, stage, duration_ms, detail=""):
        """Record one runtime timing measurement."""
        self.timing_count += 1
        self.events.append(SessionEvent(
            timestamp=self._now_iso(),
            elapsed_seconds=self._elapsed(),
            event_type="timing",
            outcome="observed",
            detail=str(detail),
            stage=str(stage),
            duration_ms=max(0.0, float(duration_ms)),
        ))

    def duration_seconds(self):
        """Return total elapsed seconds since session start."""
        return self._elapsed()

    def duration_text(self):
        """Return human-readable duration string."""
        seconds = int(self._elapsed())
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m {secs}s"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def summary(self):
        """Return a summary dict of the session."""
        return {
            "mission": self.mission,
            "autonomy_level": self.autonomy_level,
            "started": self._start_datetime.isoformat(timespec="seconds"),
            "ended": (self._end_datetime or datetime.now()).isoformat(timespec="seconds"),
            "duration_seconds": round(self._elapsed(), 1),
            "duration_text": self.duration_text(),
            "total_actions": self.action_count,
            "approvals": self.approval_count,
            "rejections": self.rejection_count,
            "corrections": self.correction_count,
            "memory_hits": self.memory_hit_count,
            "api_calls": self.api_call_count,
            "planner_rejections": self.planner_rejection_count,
            "errors": self.error_count,
            "captchas": self.captcha_count,
            "timings": self.timing_count,
        }

    def timeline(self):
        """Return events as a list of dicts for display."""
        return [event.to_dict() for event in self.events]

    def text_report(self):
        """Return a compact human-readable session report."""
        summary = self.summary()
        lines = [
            "OSROKBOT Session Report",
            "",
            f"Mission: {summary['mission']}",
            f"Started: {summary['started']}",
            f"Ended: {summary['ended']}",
            f"Duration: {summary['duration_text']}",
            f"Autonomy: L{summary['autonomy_level']}",
            "",
            "Counts:",
            f"- Actions: {summary['total_actions']}",
            f"- Approvals: {summary['approvals']}",
            f"- Corrections: {summary['corrections']}",
            f"- Planner rejections: {summary['planner_rejections']}",
            f"- API calls: {summary['api_calls']}",
            f"- Errors: {summary['errors']}",
            "",
        ]

        timings = [event for event in self.events if event.event_type == "timing" and event.duration_ms is not None]
        if timings:
            lines.append("Timing Averages:")
            stages = sorted({event.stage for event in timings if event.stage})
            for stage in stages:
                values = [float(event.duration_ms or 0.0) for event in timings if event.stage == stage]
                if not values:
                    continue
                lines.append(
                    f"- {stage}: avg={sum(values) / len(values):.1f}ms max={max(values):.1f}ms count={len(values)}"
                )
            lines.append("")

        key_events = [
            event
            for event in self.events
            if event.event_type in {"action", "approval", "rejection", "correction", "planner_rejection", "error", "captcha"}
        ]
        if key_events:
            lines.append("Key Events:")
            for event in key_events[-20:]:
                bits = [f"{event.timestamp}", event.event_type]
                if event.action_type:
                    bits.append(event.action_type)
                if event.label:
                    bits.append(event.label)
                if event.target_id:
                    bits.append(event.target_id)
                if event.outcome:
                    bits.append(event.outcome)
                if event.detail:
                    bits.append(event.detail)
                lines.append("- " + " | ".join(bits))
            lines.append("")

        lines.append("Full JSON: same filename with .json")
        lines.append("Full runtime log: data/logs/osrokbot.log")
        return "\n".join(lines) + "\n"

    def finalize(self):
        """Finalize the session and save the summary to disk.

        Returns:
            Path | None: Path to the saved session file.
        """
        self._end_datetime = datetime.now()
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            filename = self._start_datetime.strftime("session_%Y%m%d_%H%M%S.json")
            path = self.output_dir / filename
            payload = {
                "summary": self.summary(),
                "events": self.timeline(),
            }
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            path.with_suffix(".txt").write_text(self.text_report(), encoding="utf-8")
            self.retention_manager.prune_directory(self.output_dir, self.retention_policy)
            LOGGER.info("Session log saved: %s", path)
            return path
        except Exception as exc:
            LOGGER.error("Failed to save session log: %s", exc)
            return None
