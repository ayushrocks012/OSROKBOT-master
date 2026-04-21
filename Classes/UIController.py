"""View-model/controller for the Agent Supervisor Console.

This module owns runtime-facing UI logic: planner approval state, YOLO warmup,
session logging, mission history, and automation lifecycle orchestration.
`UI.py` should stay focused on widget composition, styling, and user input
forwarding.
"""

from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config_manager import ConfigManager
from context import Context
from gameplay_teaching import (
    DEFAULT_TEACHING_PROFILE,
    get_profile,
    profile_options,
    teaching_questions_text,
)
from logging_config import bind_log_context, get_logger, reset_log_context
from model_manager import ModelManager, yolo_download_required
from object_detector import create_detector
from planner_decision_policy import decision_requires_manual_fix
from PyQt5 import QtCore, QtWidgets
from run_handoff import reconcile_latest_runtime_run
from runtime_composition import DEFAULT_MISSION, SupervisorRuntimeComposition
from session_logger import SessionLogger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGGER = get_logger(__name__)
MAX_MISSION_HISTORY = 10

MISSION_PRESETS = [
    "Farm the nearest useful resource safely.",
    "Gather wood (level 4+ preferred).",
    "Complete daily objectives.",
    "Navigate visible prompts and wait when uncertain.",
    "Answer Lyceum questions.",
    "Farm the nearest level 4 wood node without spending action points.",
    "Continue the current gathering flow safely. Stop if a CAPTCHA appears.",
]

ERROR_GUIDANCE = {
    "Interception unavailable": (
        "Install the Oblita Interception driver as Administrator and reboot. "
        "See README setup guidance for the supported workflow."
    ),
    "Game not foreground": (
        "Bring the Rise of Kingdoms window to the foreground before resuming automation."
    ),
    "No planner action pending": (
        "The planner is not currently waiting for human approval."
    ),
    "Captcha detected": (
        "A CAPTCHA was detected. Solve it manually in the game window, then resume automation."
    ),
    "YOLO weights unavailable": (
        "The configured YOLO weights are unavailable. Check Settings and warmup status."
    ),
}
_RUNTIME_STATUS_PATTERNS = (
    ("ai recovering", ("AI recovering", "accent", "")),
    ("learning", ("Learning from feedback", "success", "")),
    ("using memory", ("Using trusted memory", "success", "")),
    ("game not foreground", ("Game not foreground - paused", "warning", ERROR_GUIDANCE["Game not foreground"])),
    ("planner approval needed", ("Agent awaiting approval", "accent", "")),
    ("planner trusted", ("Trusted action auto-approved", "success", "")),
)
_TIMELINE_ICON_MAP = {
    "action": "RUN",
    "approval": "OK",
    "rejection": "NO",
    "correction": "FIX",
    "error": "ERR",
    "warning": "WARN",
    "captcha": "CAP",
    "planner_rejection": "DROP",
    "info": "INFO",
    "timing": "TIME",
    "state": "STATE",
    "decision": "PLAN",
    "terminal": "END",
}


@dataclass(slots=True)
class IntentCardState:
    """View-model payload for the approval gate intent card."""

    visible: bool = False
    title: str = "Awaiting approval"
    action_text: str = ""
    target_text: str = ""
    confidence: float = 0.0
    confidence_tone: str = "danger"
    confidence_caption: str = "0%"
    reason_text: str = ""
    coordinates_text: str = ""
    shortcut_hint: str = ""
    fix_required: bool = False


@dataclass(slots=True)
class SupervisorSnapshot:
    """One immutable-ish snapshot consumed by the PyQt view."""

    mode: str = "command"
    state_text: str = "Ready"
    state_icon: str = "--"
    status_text: str = "Standing by"
    status_tone: str = "info"
    status_detail: str = ""
    mission_text: str = DEFAULT_MISSION
    mission_options: list[str] = field(default_factory=list)
    autonomy_level: int = 1
    teaching_mode_enabled: bool = False
    teaching_profile_name: str = DEFAULT_TEACHING_PROFILE
    teaching_profile_options: list[tuple[str, str]] = field(default_factory=list)
    teaching_notes: str = ""
    teaching_prompt_text: str = ""
    elapsed_text: str = "00:00"
    is_running: bool = False
    is_paused: bool = False
    yolo_ready: bool = True
    can_start: bool = True
    can_pause: bool = False
    can_stop: bool = False
    start_tooltip: str = "Start (F5)"
    pause_tooltip: str = "Pause / Resume (F6)"
    intent: IntentCardState = field(default_factory=IntentCardState)
    dashboard_summary: dict[str, str] = field(default_factory=dict)
    timeline_lines: list[str] = field(default_factory=list)


def _format_elapsed(seconds: float) -> str:
    """Render one elapsed-duration value for the supervisor snapshot."""

    total_seconds = max(0, int(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _bounded_autonomy(level: int) -> int:
    """Clamp autonomy selection to the supported `L1`-`L3` range."""

    return max(1, min(3, int(level)))


def _history_entries(raw: str) -> list[str]:
    """Decode persisted mission history into normalized strings."""

    try:
        decoded = json.loads(raw) if raw else []
    except Exception:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item).strip() for item in decoded if str(item).strip()]


def _unique_options(items: list[str]) -> list[str]:
    """Preserve order while deduplicating mission-option strings."""

    seen: set[str] = set()
    options: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            options.append(item)
    return options


def _confidence_tone(confidence: float) -> str:
    """Map planner confidence to the intent card tone."""

    if confidence >= 0.85:
        return "success"
    if confidence >= 0.70:
        return "warning"
    return "danger"


def _coordinates_text(absolute_x: Any, absolute_y: Any) -> str:
    """Render pending-approval absolute coordinates for the intent card."""

    if absolute_x is None or absolute_y is None:
        return "Screen target unavailable"
    return f"Screen target: {int(absolute_x)}, {int(absolute_y)}"


class UIController(QtCore.QObject):
    """Own automation-facing UI state and emit view-model snapshots."""

    snapshot_changed = QtCore.pyqtSignal(object)
    planner_overlay_requested = QtCore.pyqtSignal(dict)
    planner_overlay_cleared = QtCore.pyqtSignal()
    fix_overlay_requested = QtCore.pyqtSignal(dict)
    fix_overlay_cleared = QtCore.pyqtSignal()
    notification_requested = QtCore.pyqtSignal(str, str, object)

    def __init__(
        self,
        window_title: str,
        delay: float = 0.0,
        *,
        composition: SupervisorRuntimeComposition | None = None,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.target_title = window_title
        self._composition = composition or SupervisorRuntimeComposition(window_title, delay=delay)
        self._window_handler = self._composition.window_handler
        self._detector = self._composition.detector
        self._vision_memory = self._composition.vision_memory
        self._detection_dataset = self._composition.detection_dataset
        self.OS_ROKBOT = self._composition.build_bot()
        self.OS_ROKBOT.signal_emitter.pause_toggled.connect(self.handle_pause_toggled)
        self.OS_ROKBOT.signal_emitter.state_changed.connect(self.handle_runtime_state_changed)
        self.OS_ROKBOT.signal_emitter.planner_decision.connect(self.handle_planner_decision)
        self.OS_ROKBOT.signal_emitter.yolo_weights_ready.connect(self.handle_yolo_weights_ready)
        self.OS_ROKBOT.signal_emitter.run_finished.connect(self.handle_run_finished)

        self.action_sets = self._composition.create_action_sets(self.OS_ROKBOT)
        self._background_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OSROKBOT-UI")
        self._yolo_warmup_future: Future[Any] | None = None
        self._current_context: Context | None = None
        self._session_logger: SessionLogger | None = None
        self._logging_context_token: Any | None = None
        self._pending_payload: dict[str, Any] | None = None
        self._fix_capture_active = False
        self._session_active = False
        self._session_finalized = False
        self._finalized_summary: dict[str, Any] | None = None
        self._finalized_timeline: list[dict[str, Any]] | None = None
        self._last_runtime_state = "Ready"
        self._status_text = "Standing by"
        self._status_tone = "info"
        self._status_detail = ""
        self._mission_text = DEFAULT_MISSION
        self._mission_options = self._load_mission_options()
        self._autonomy_level = self._load_autonomy_level()
        self._teaching_mode_enabled = self._load_teaching_mode_enabled()
        self._teaching_profile_name = self._load_teaching_profile_name()
        self._teaching_profile_options = profile_options()
        self._teaching_notes = self._load_teaching_notes()
        self._teaching_prompt_text = teaching_questions_text(self._teaching_profile_name)
        self._yolo_ready = True
        self._start_tooltip = "Start (F5)"

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.timeout.connect(self._emit_snapshot)
        self._refresh_timer.start(1000)

        reconcile_latest_runtime_run()
        self._emit_snapshot()
        self.begin_yolo_warmup()

    @property
    def current_context(self) -> Context | None:
        """Expose the current automation context for compatibility."""

        return self._current_context

    @property
    def session_logger(self) -> SessionLogger | None:
        """Expose the current or most recent session logger."""

        return self._session_logger

    @staticmethod
    def yolo_warmup_required(config: ConfigManager | None = None) -> bool:
        """Return whether YOLO weights still need to be prepared."""

        return yolo_download_required(config)

    @staticmethod
    def _mission_history(config: ConfigManager) -> list[str]:
        """Return the persisted mission history for the supervisor console."""

        return _history_entries(str(config.get("MISSION_HISTORY", "") or ""))

    @staticmethod
    def _resolved_mission_options(history: list[str]) -> list[str]:
        """Merge persisted mission history with shipped presets."""

        return _unique_options(history + MISSION_PRESETS)

    def _load_mission_options(self) -> list[str]:
        """Load configured mission history and resolve the current default mission."""

        config = ConfigManager()
        history = self._mission_history(config)
        options = self._resolved_mission_options(history)
        configured_goal = str(config.get("PLANNER_GOAL", "") or "").strip()
        self._mission_text = configured_goal or (options[0] if options else DEFAULT_MISSION)
        return options

    def _save_mission_to_history(self, mission: str) -> None:
        """Persist one mission at the top of the bounded mission-history list."""

        clean_mission = str(mission).strip()
        if not clean_mission:
            return

        config = ConfigManager()
        history = self._mission_history(config)

        filtered = [str(item).strip() for item in history if str(item).strip() and str(item).strip() != clean_mission]
        filtered.insert(0, clean_mission)
        config.set_many({"MISSION_HISTORY": json.dumps(filtered[:MAX_MISSION_HISTORY])})
        self._mission_options = self._load_mission_options()

    @staticmethod
    def _load_autonomy_level() -> int:
        """Load the persisted autonomy level and clamp it to the supported range."""

        try:
            configured = int(ConfigManager().get("PLANNER_AUTONOMY_LEVEL", "1"))
        except (TypeError, ValueError):
            configured = 1
        return _bounded_autonomy(configured)

    @staticmethod
    def _load_teaching_mode_enabled() -> bool:
        """Load the persisted teaching-mode toggle."""

        raw = str(ConfigManager().get("TEACHING_MODE_ENABLED", "0") or "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _load_teaching_profile_name() -> str:
        """Load the persisted gameplay teaching profile name."""

        configured = str(
            ConfigManager().get("TEACHING_PROFILE_NAME", DEFAULT_TEACHING_PROFILE) or DEFAULT_TEACHING_PROFILE
        )
        return get_profile(configured).name

    @staticmethod
    def _load_teaching_notes() -> str:
        """Load the persisted operator gameplay notes."""

        return str(ConfigManager().get("TEACHING_NOTES", "") or "").strip()

    def set_autonomy_level(self, level: int) -> None:
        """Update and persist the selected autonomy level."""

        self._autonomy_level = max(1, min(3, int(level)))
        ConfigManager().set_many({"PLANNER_AUTONOMY_LEVEL": str(self._autonomy_level)})
        self._emit_snapshot()

    def set_teaching_mode_enabled(self, enabled: bool) -> None:
        """Update the in-memory teaching-mode toggle shown by the supervisor UI."""

        self._teaching_mode_enabled = bool(enabled)
        self._emit_snapshot()

    def set_teaching_profile_name(self, profile_name: str) -> None:
        """Update the selected gameplay teaching profile in memory."""

        resolved = get_profile(profile_name).name
        self._teaching_profile_name = resolved
        self._teaching_prompt_text = teaching_questions_text(resolved)
        self._emit_snapshot()

    def set_teaching_notes(self, notes: str) -> None:
        """Update the operator-authored gameplay notes in memory."""

        self._teaching_notes = str(notes or "").strip()
        self._emit_snapshot()

    def _set_status(self, text: str, tone: str, detail: str = "") -> None:
        """Store the latest operator-facing status banner payload."""

        self._status_text = text
        self._status_tone = tone
        self._status_detail = detail

    def begin_yolo_warmup(self) -> None:
        """Start background YOLO preparation if required."""

        if self._yolo_warmup_future and not self._yolo_warmup_future.done():
            return
        if not self.yolo_warmup_required():
            self._yolo_ready = True
            self._start_tooltip = "Start (F5)"
            self._emit_snapshot()
            return

        self._yolo_ready = False
        self._start_tooltip = "Waiting for YOLO weights warmup"
        self._set_status(
            "Preparing YOLO weights",
            "warning",
            "A configured YOLO weights file or URL is being prepared in the background.",
        )
        self._emit_snapshot()
        self._yolo_warmup_future = self._background_executor.submit(ModelManager().ensure_yolo_weights)
        self._yolo_warmup_future.add_done_callback(self._emit_yolo_ready_from_future)

    def _emit_yolo_ready_from_future(self, future: Future[Any]) -> None:
        try:
            weights_path = future.result()
        except Exception as exc:
            LOGGER.warning("YOLO weights warmup failed: %s", exc)
            self.OS_ROKBOT.signal_emitter.yolo_weights_ready.emit(False, str(exc))
            return
        self.OS_ROKBOT.signal_emitter.yolo_weights_ready.emit(bool(weights_path), str(weights_path or "YOLO weights download failed"))

    @QtCore.pyqtSlot(bool, str)
    def handle_yolo_weights_ready(self, success: bool, message: str) -> None:
        """Update controller state after background YOLO warmup completes."""

        self._yolo_warmup_future = None
        self._yolo_ready = bool(success)
        if success:
            try:
                self._detector = create_detector()
                self._composition.detector = self._detector
                self.OS_ROKBOT.detector = self._detector
            except Exception as exc:
                LOGGER.warning("Detector refresh failed after YOLO warmup: %s", exc)
                self._yolo_ready = False
                message = str(exc)

        if self._yolo_ready:
            self._start_tooltip = "Start (F5)"
            if not self.OS_ROKBOT.is_running:
                self._set_status("Ready", "info", "")
        else:
            self._start_tooltip = "YOLO weights unavailable; check Settings"
            self._set_status("YOLO weights unavailable", "danger", str(message))
        self._emit_snapshot()

    def refresh_after_settings(self) -> None:
        """Refresh persisted selections after the Settings dialog changes."""

        self._mission_options = self._load_mission_options()
        self._autonomy_level = self._load_autonomy_level()
        self._teaching_mode_enabled = self._load_teaching_mode_enabled()
        self._teaching_profile_name = self._load_teaching_profile_name()
        self._teaching_notes = self._load_teaching_notes()
        self._teaching_prompt_text = teaching_questions_text(self._teaching_profile_name)
        self.begin_yolo_warmup()
        self._emit_snapshot()

    def _start_selection(
        self,
        mission_text: str | None,
        autonomy_level: int | None,
        teaching_mode_enabled: bool | None,
        teaching_profile_name: str | None,
        teaching_notes: str | None,
    ) -> tuple[str, int, bool, str, str]:
        """Normalize the mission, autonomy, and teaching-mode selections for a new run."""

        mission = str(mission_text or self._mission_text or DEFAULT_MISSION).strip() or DEFAULT_MISSION
        selected_autonomy = _bounded_autonomy(autonomy_level or self._autonomy_level)
        selected_teaching_mode = self._teaching_mode_enabled if teaching_mode_enabled is None else bool(teaching_mode_enabled)
        selected_profile = get_profile(teaching_profile_name or self._teaching_profile_name).name
        selected_notes = str(teaching_notes if teaching_notes is not None else self._teaching_notes).strip()
        return mission, selected_autonomy, selected_teaching_mode, selected_profile, selected_notes

    def _start_blocker(self) -> tuple[str, str, str] | None:
        """Return the status payload describing why a new run cannot start yet."""

        if not self._yolo_ready:
            return "YOLO weights unavailable", "danger", ERROR_GUIDANCE["YOLO weights unavailable"]
        if self.OS_ROKBOT.is_running or not self.OS_ROKBOT.all_threads_joined:
            return (
                "Finishing previous run",
                "warning",
                "Wait for the current worker thread to join before restarting.",
            )
        return None

    def _persist_start_selection(
        self,
        mission: str,
        autonomy_level: int,
        teaching_mode_enabled: bool,
        teaching_profile_name: str,
        teaching_notes: str,
    ) -> None:
        """Persist one accepted start selection and prepare the game window."""

        self._mission_text = mission
        self._autonomy_level = autonomy_level
        self._teaching_mode_enabled = bool(teaching_mode_enabled)
        self._teaching_profile_name = get_profile(teaching_profile_name).name
        self._teaching_notes = str(teaching_notes or "").strip()
        self._teaching_prompt_text = teaching_questions_text(self._teaching_profile_name)
        if self.OS_ROKBOT.is_paused():
            self.OS_ROKBOT.toggle_pause()

        self._window_handler.activate_window(self.target_title)
        ConfigManager().set_many(
            {
                "PLANNER_GOAL": mission,
                "PLANNER_AUTONOMY_LEVEL": str(autonomy_level),
                "TEACHING_MODE_ENABLED": "1" if self._teaching_mode_enabled else "0",
                "TEACHING_PROFILE_NAME": self._teaching_profile_name,
                "TEACHING_NOTES": self._teaching_notes,
            }
        )
        self._save_mission_to_history(mission)

    def _prepare_runtime_session(
        self,
        mission: str,
        autonomy_level: int,
        teaching_mode_enabled: bool,
        teaching_profile_name: str,
        teaching_notes: str,
    ) -> Context:
        """Create the per-run session logger, logging context, and runtime `Context`."""

        self._session_logger = SessionLogger(mission=mission, autonomy_level=autonomy_level)
        if self._logging_context_token is not None:
            reset_log_context(self._logging_context_token)
        self._logging_context_token = bind_log_context(**self._session_logger.log_context_fields())
        context = self._composition.create_context(
            bot=self.OS_ROKBOT,
            session_logger=self._session_logger,
            planner_goal=mission,
            planner_autonomy_level=autonomy_level,
            teaching_mode_enabled=teaching_mode_enabled,
            teaching_profile_name=teaching_profile_name,
            teaching_notes=teaching_notes,
        )
        self._current_context = context
        self._pending_payload = None
        self._fix_capture_active = False
        self._session_finalized = False
        return context

    def _set_start_result(self, *, started: bool, mission: str) -> None:
        """Update controller state after the runner accepts or rejects start."""

        if started:
            self._session_logger.record_info(f"Session started: {mission}")
            self._session_active = True
            self._finalized_summary = None
            self._finalized_timeline = None
            self._last_runtime_state = "Running"
            self._set_status("Running", "success", "")
            return

        self._set_status("Unable to start automation", "danger", "OSROKBOT refused to start the run.")
        self._current_context = None
        self._session_active = True
        self._finalize_session(status="failed", end_reason="start_refused", detail="OSROKBOT refused to start the run.")

    def start_automation(
        self,
        mission_text: str | None = None,
        autonomy_level: int | None = None,
        teaching_mode_enabled: bool | None = None,
        teaching_profile_name: str | None = None,
        teaching_notes: str | None = None,
    ) -> None:
        """Start a planner-first automation run from the selected mission."""

        mission, selected_autonomy, selected_teaching_mode, selected_profile, selected_notes = self._start_selection(
            mission_text,
            autonomy_level,
            teaching_mode_enabled,
            teaching_profile_name,
            teaching_notes,
        )
        blocker = self._start_blocker()
        if blocker is not None:
            self._set_status(*blocker)
            self._emit_snapshot()
            return

        self._persist_start_selection(
            mission,
            selected_autonomy,
            selected_teaching_mode,
            selected_profile,
            selected_notes,
        )

        action_group = self.action_sets.dynamic_planner()
        if not action_group:
            self._set_status("Planner startup failed", "danger", "The dynamic planner action set could not be created.")
            self._emit_snapshot()
            return

        context = self._prepare_runtime_session(
            mission,
            selected_autonomy,
            selected_teaching_mode,
            selected_profile,
            selected_notes,
        )
        self._set_start_result(started=self.OS_ROKBOT.start([action_group], context), mission=mission)
        self._emit_snapshot()

    def _finalize_session(self, *, status: str | None = None, end_reason: str | None = None, detail: str = "") -> None:
        if not self._session_logger or self._session_finalized:
            return
        path = self._session_logger.finalize(status=status, end_reason=end_reason, detail=detail)
        self._finalized_summary = self._session_logger.summary()
        self._finalized_timeline = self._session_logger.timeline()
        self._session_active = False
        self._session_finalized = True
        if self._logging_context_token is not None:
            reset_log_context(self._logging_context_token)
            self._logging_context_token = None
        if path:
            self.notification_requested.emit(
                "OSROKBOT - Session Saved",
                f"Session log saved to {Path(path).name}",
                QtWidgets.QSystemTrayIcon.Information,
            )

    def _record_runtime_state(self, state_text: str) -> None:
        """Mirror the latest runtime state into the active grouped session log."""

        if self._session_logger and self._session_active:
            self._session_logger.record_state(state_text)

    def _mark_session_terminal(self, status: str, end_reason: str, detail: str = "") -> None:
        """Mark the active session terminal without finalizing artifacts twice."""

        if self._session_logger and self._session_active and not self._session_finalized:
            self._session_logger.mark_terminal(status, end_reason, detail=detail)

    def _active_context(self) -> Context | None:
        if not self._current_context:
            return None
        if not self._current_context.pending_planner_decision():
            return None
        return self._current_context

    @staticmethod
    def _pending_requires_fix_payload(pending: dict[str, Any] | None) -> bool:
        if not pending:
            return False
        if "fix_required" in pending:
            return bool(pending.get("fix_required"))
        return decision_requires_manual_fix(pending.get("decision", {}))

    def _runtime_status_from_patterns(self, lowered: str) -> tuple[str, str, str] | None:
        """Return one generic status mapping for a runtime-state substring match."""

        for needle, status_payload in _RUNTIME_STATUS_PATTERNS:
            if needle in lowered:
                return status_payload
        return None

    def _handle_captcha_state(self, previous_lowered: str) -> None:
        """Apply CAPTCHA pause status and emit a one-time operator notification."""

        self._set_status("Captcha detected - paused", "warning", ERROR_GUIDANCE["Captcha detected"])
        if "captcha detected" not in previous_lowered:
            self.notification_requested.emit(
                "OSROKBOT - CAPTCHA",
                "A CAPTCHA was detected. Solve it manually before resuming.",
                QtWidgets.QSystemTrayIcon.Warning,
            )

    def _handle_interception_unavailable(self) -> None:
        """Apply the interception failure status and mark the session terminal."""

        self._set_status("Interception unavailable", "danger", ERROR_GUIDANCE["Interception unavailable"])
        self._mark_session_terminal("failed", "interception_unavailable", ERROR_GUIDANCE["Interception unavailable"])

    def _handle_mission_complete(self, previous_lowered: str) -> None:
        """Apply mission-complete status and emit a one-time completion notification."""

        self._set_status("Mission complete", "success", "")
        self._mark_session_terminal("success", "mission_complete", "The task graph reported mission completion.")
        if "mission complete" not in previous_lowered:
            self.notification_requested.emit(
                "OSROKBOT - Complete",
                "The current mission has completed.",
                QtWidgets.QSystemTrayIcon.Information,
            )

    def approve_pending_action(self) -> None:
        """Approve the pending planner action if it is execution-ready."""

        context = self._active_context()
        if not context:
            self._set_status("No planner action pending", "warning", ERROR_GUIDANCE["No planner action pending"])
            self._emit_snapshot()
            return
        pending = context.pending_planner_decision() or {}
        if self._pending_requires_fix_payload(pending):
            self._set_status(
                "Fix required before OK",
                "warning",
                "Low-confidence pointer targets must be corrected with Fix or rejected with No.",
            )
            self._emit_snapshot()
            return

        label = str(pending.get("decision", {}).get("label", ""))
        context.resolve_planner_decision(True)
        if self._session_logger:
            self._session_logger.record_approval(label)
        self._pending_payload = None
        self._fix_capture_active = False
        self.planner_overlay_cleared.emit()
        self.fix_overlay_cleared.emit()
        self._set_status("Planner action approved", "success", "")
        self._emit_snapshot()

    def reject_pending_action(self) -> None:
        """Reject the pending planner action."""

        context = self._active_context()
        if not context:
            self._set_status("No planner action pending", "warning", ERROR_GUIDANCE["No planner action pending"])
            self._emit_snapshot()
            return

        pending = context.pending_planner_decision() or {}
        label = str(pending.get("decision", {}).get("label", ""))
        context.resolve_planner_decision(False)
        if self._session_logger:
            self._session_logger.record_rejection(label)
        self._pending_payload = None
        self._fix_capture_active = False
        self.planner_overlay_cleared.emit()
        self.fix_overlay_cleared.emit()
        self._set_status("Planner action rejected", "warning", "")
        self._emit_snapshot()

    def begin_fix_capture(self) -> None:
        """Request a blocking correction overlay over the active game window."""

        context = self._active_context()
        if not context:
            self._set_status("No planner action pending", "warning", ERROR_GUIDANCE["No planner action pending"])
            self._emit_snapshot()
            return

        pending = context.pending_planner_decision() or {}
        rect = pending.get("window_rect", {})
        if not int(rect.get("width", 0)) or not int(rect.get("height", 0)):
            self._set_status("Fix unavailable", "danger", "The planner did not supply a valid game window rectangle.")
            self._emit_snapshot()
            return

        decision = pending.get("decision", {})
        prompt = f"Fix {decision.get('action_type', 'click')} target for {decision.get('label', 'target')}"
        self._fix_capture_active = True
        self._set_status("Click the corrected point", "accent", "The console is waiting for one precise click over the game window.")
        self.planner_overlay_cleared.emit()
        self.fix_overlay_requested.emit({"window_rect": rect, "prompt_text": prompt})
        self._emit_snapshot()

    def apply_fix_selection(self, normalized_point: dict[str, float]) -> None:
        """Resolve the pending planner approval with a corrected normalized point."""

        context = self._active_context()
        if not context:
            self._fix_capture_active = False
            self.fix_overlay_cleared.emit()
            self._emit_snapshot()
            return

        pending = context.pending_planner_decision() or {}
        label = str(pending.get("decision", {}).get("label", ""))
        context.resolve_planner_decision(True, corrected_point=normalized_point)
        if self._session_logger:
            self._session_logger.record_correction(label)
        self._pending_payload = None
        self._fix_capture_active = False
        self.fix_overlay_cleared.emit()
        self.planner_overlay_cleared.emit()
        self._set_status("Planner correction saved", "success", "")
        self._emit_snapshot()

    def cancel_fix_capture(self) -> None:
        """Cancel the current blocking correction capture and restore approval preview."""

        self._fix_capture_active = False
        self.fix_overlay_cleared.emit()
        if self._pending_payload:
            self.planner_overlay_requested.emit(self._overlay_payload_from_pending(self._pending_payload))
            self._set_status("Approval still pending", "accent", "Review the agent intent card or press Fix again.")
        self._emit_snapshot()

    def stop_automation(
        self,
        *,
        status: str = "interrupted",
        end_reason: str = "operator_stop",
        detail: str = "Operator requested stop.",
    ) -> None:
        """Stop the current automation run and finalize the active session log."""

        if self._current_context and self._current_context.pending_planner_decision():
            self._current_context.resolve_planner_decision(False)

        self._mark_session_terminal(status, end_reason, detail=detail)
        self.OS_ROKBOT.stop()
        self._pending_payload = None
        self._fix_capture_active = False
        self.planner_overlay_cleared.emit()
        self.fix_overlay_cleared.emit()
        if not self.OS_ROKBOT.is_running:
            self._finalize_session(status=status, end_reason=end_reason, detail=detail)
            self._current_context = None
            self._last_runtime_state = "Ready"
            self._set_status("Ready", "info", "")
        else:
            self._set_status("Stopping", "warning", detail)
        self._emit_snapshot()

    def toggle_pause(self) -> None:
        """Toggle the OSROKBOT pause event."""

        self.OS_ROKBOT.toggle_pause()
        if self.OS_ROKBOT.is_paused():
            self._set_status("Paused", "warning", "")
        self._emit_snapshot()

    @QtCore.pyqtSlot(bool)
    def handle_pause_toggled(self, is_paused: bool) -> None:
        """Update controller state after the runner pause event changes."""

        if is_paused:
            self._set_status("Paused", "warning", "")
        elif self.OS_ROKBOT.is_running:
            self._set_status("Running", "success", "")
        else:
            self._set_status("Ready", "info", "")
        self._emit_snapshot()

    @QtCore.pyqtSlot(str)
    def handle_runtime_state_changed(self, state_text: str) -> None:
        """Map runtime state text into operator-facing status."""

        text = str(state_text or "").strip() or "Ready"
        previous_state = self._last_runtime_state
        self._last_runtime_state = text
        self._record_runtime_state(text)
        lowered = text.lower()
        previous_lowered = previous_state.lower()

        if "captcha detected" in lowered:
            self._handle_captcha_state(previous_lowered)
        elif "interception unavailable" in lowered:
            self._handle_interception_unavailable()
        elif "mission complete" in lowered:
            self._handle_mission_complete(previous_lowered)
        else:
            status_payload = self._runtime_status_from_patterns(lowered)
            if status_payload is not None:
                self._set_status(*status_payload)
            elif self.OS_ROKBOT.is_running:
                self._set_status("Running", "success", "")
            else:
                self._set_status("Ready", "info", "")
        self._emit_snapshot()

    def _intent_shortcut_hint(self, fix_required: bool) -> str:
        """Return the shortcut hint shown on the planner intent card."""

        return "OK disabled until Fix or No" if fix_required else "F8 OK   F9 No   F10 Fix"

    def _pending_target_text(self, decision: dict[str, Any]) -> str:
        """Render the label/target-id text shown on the planner intent card."""

        return f"{decision.get('label', 'target')} ({decision.get('target_id', 'manual') or 'manual'})"

    def _timeline(self) -> list[dict[str, Any]]:
        """Return the active or finalized timeline used by the dashboard."""

        if self._session_active or self._finalized_timeline is None:
            return self._session_logger.timeline()
        return self._finalized_timeline

    def _elapsed_seconds(self) -> float:
        """Return the current elapsed duration for the supervisor snapshot."""

        if self._session_logger and self._session_active:
            return self._session_logger.duration_seconds()
        if self._finalized_summary is not None:
            return float(self._finalized_summary.get("duration_seconds", 0.0))
        return 0.0

    def _snapshot_flags(self) -> tuple[bool, bool, bool, bool, bool]:
        """Return the running/pause control flags for the supervisor snapshot."""

        is_running = bool(self.OS_ROKBOT.is_running)
        is_paused = bool(self.OS_ROKBOT.is_paused())
        can_start = self._yolo_ready and not is_running and bool(self.OS_ROKBOT.all_threads_joined)
        can_pause = is_running or is_paused
        can_stop = is_running or is_paused or self._current_context is not None
        return is_running, is_paused, can_start, can_pause, can_stop

    @QtCore.pyqtSlot(dict)
    def handle_planner_decision(self, payload: dict[str, Any]) -> None:
        """Stage one pending approval payload and request the preview overlay."""

        if not isinstance(payload, dict):
            return
        self._pending_payload = payload
        self._fix_capture_active = False
        overlay_payload = self._overlay_payload_from_pending(payload)
        if overlay_payload.get("absolute_x") is not None and overlay_payload.get("absolute_y") is not None:
            self.planner_overlay_requested.emit(overlay_payload)
        self._set_status(
            "Fix required" if overlay_payload.get("fix_required") else "Awaiting approval",
            "warning" if overlay_payload.get("fix_required") else "accent",
            "Low-confidence pointer targets must be corrected before OK." if overlay_payload.get("fix_required") else "",
        )
        self._emit_snapshot()

    @QtCore.pyqtSlot(dict)
    def handle_run_finished(self, payload: dict[str, Any]) -> None:
        """Finalize the active session after the runner exits."""

        status = str(payload.get("status", "interrupted") or "interrupted")
        end_reason = str(payload.get("end_reason", "runner_stopped_without_terminal_reason") or "runner_stopped_without_terminal_reason")
        detail = str(payload.get("detail", "") or "")
        self._finalize_session(status=status, end_reason=end_reason, detail=detail)
        self._current_context = None
        self._pending_payload = None
        self._fix_capture_active = False
        self.planner_overlay_cleared.emit()
        self.fix_overlay_cleared.emit()
        if status == "failed":
            self._set_status("Run failed", "danger", end_reason)
        elif status == "success":
            self._set_status("Ready", "info", "")
        else:
            self._set_status("Ready", "info", "")
        self._last_runtime_state = "Ready"
        self._emit_snapshot()

    def _overlay_payload_from_pending(self, pending: dict[str, Any]) -> dict[str, Any]:
        decision = pending.get("decision", {})
        fix_required = self._pending_requires_fix_payload(pending)
        return {
            "absolute_x": pending.get("absolute_x"),
            "absolute_y": pending.get("absolute_y"),
            "label": str(decision.get("label", "target") or "target"),
            "confidence": float(decision.get("confidence", 0.0) or 0.0),
            "action_type": str(decision.get("action_type", "click") or "click"),
            "window_rect": pending.get("window_rect", {}),
            "detections": pending.get("detections", []),
            "target_id": str(decision.get("target_id", "") or ""),
            "shortcut_hint": "Waiting for [F10] or [F9]" if fix_required else "Waiting for [F8], [F9], or [F10]",
            "fix_required": fix_required,
        }

    def _state_icon(self) -> str:
        lowered = self._last_runtime_state.lower()
        if "mission complete" in lowered:
            return "OK"
        if "captcha" in lowered or self.OS_ROKBOT.is_paused():
            return "II"
        if self._pending_payload:
            return "!"
        if "recover" in lowered:
            return "R"
        if self.OS_ROKBOT.is_running:
            return "ON"
        return "--"

    def _ui_mode(self) -> str:
        if self._pending_payload:
            return "approval"
        if self.OS_ROKBOT.is_running and not self.OS_ROKBOT.is_paused() and self._autonomy_level >= 2:
            return "compact"
        return "command"

    def _intent_state(self) -> IntentCardState:
        """Return the planner intent card state for the current pending action."""

        if not self._pending_payload:
            return IntentCardState()

        pending = self._pending_payload
        decision = pending.get("decision", {})
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        fix_required = self._pending_requires_fix_payload(pending)
        return IntentCardState(
            visible=True,
            title="Fix required" if fix_required else "Awaiting approval",
            action_text=str(decision.get("action_type", "click") or "click").title(),
            target_text=self._pending_target_text(decision),
            confidence=confidence,
            confidence_tone=_confidence_tone(confidence),
            confidence_caption=f"{confidence:.0%} confidence",
            reason_text=str(decision.get("reason", "") or "No planner reason supplied."),
            coordinates_text=_coordinates_text(pending.get("absolute_x"), pending.get("absolute_y")),
            shortcut_hint=self._intent_shortcut_hint(fix_required),
            fix_required=fix_required,
        )

    def _dashboard_summary(self) -> dict[str, str]:
        if not self._session_logger:
            return {
                "Mission": self._mission_text,
                "Autonomy": f"L{self._autonomy_level}",
                "Duration": "00:00",
                "Actions": "0",
                "Approvals": "0",
                "Corrections": "0",
                "API Calls": "0",
                "Errors": "0",
                "CAPTCHAs": "0",
            }

        summary = self._session_logger.summary() if self._session_active or not self._finalized_summary else self._finalized_summary
        return {
            "Mission": str(summary["mission"])[:48] or self._mission_text,
            "Autonomy": f"L{summary['autonomy_level']}",
            "Duration": _format_elapsed(float(summary["duration_seconds"])),
            "Actions": str(summary["total_actions"]),
            "Approvals": str(summary["approvals"]),
            "Corrections": str(summary["corrections"]),
            "API Calls": str(summary["api_calls"]),
            "Errors": str(summary["errors"]),
            "CAPTCHAs": str(summary["captchas"]),
        }

    def _timeline_lines(self) -> list[str]:
        """Render the dashboard timeline lines for the active or finalized session."""

        if not self._session_logger:
            return ["No session activity yet."]

        lines: list[str] = []
        for event in self._timeline():
            icon = _TIMELINE_ICON_MAP.get(str(event.get("event_type", "")), "LOG")
            elapsed = f"+{float(event.get('elapsed_seconds', 0.0)):>4.0f}s"
            detail = str(event.get("label") or event.get("detail") or event.get("action_type") or "").strip()
            lines.append(f"{elapsed}  {icon:<4}  {detail or '(no detail)'}")
        return lines or ["No session activity yet."]

    def snapshot(self) -> SupervisorSnapshot:
        """Return the latest view-model snapshot."""

        elapsed = self._elapsed_seconds()
        is_running, is_paused, can_start, can_pause, can_stop = self._snapshot_flags()
        return SupervisorSnapshot(
            mode=self._ui_mode(),
            state_text=self._last_runtime_state,
            state_icon=self._state_icon(),
            status_text=self._status_text,
            status_tone=self._status_tone,
            status_detail=self._status_detail,
            mission_text=self._mission_text,
            mission_options=list(self._mission_options),
            autonomy_level=self._autonomy_level,
            teaching_mode_enabled=self._teaching_mode_enabled,
            teaching_profile_name=self._teaching_profile_name,
            teaching_profile_options=list(self._teaching_profile_options),
            teaching_notes=self._teaching_notes,
            teaching_prompt_text=self._teaching_prompt_text,
            elapsed_text=_format_elapsed(elapsed),
            is_running=is_running,
            is_paused=is_paused,
            yolo_ready=self._yolo_ready,
            can_start=can_start,
            can_pause=can_pause,
            can_stop=can_stop,
            start_tooltip=self._start_tooltip,
            pause_tooltip="Resume (F6)" if is_paused else "Pause (F6)",
            intent=self._intent_state(),
            dashboard_summary=self._dashboard_summary(),
            timeline_lines=self._timeline_lines(),
        )

    def _emit_snapshot(self) -> None:
        self.snapshot_changed.emit(self.snapshot())

    def shutdown(self) -> None:
        """Stop automation and release controller-owned background resources."""

        self.stop_automation(status="interrupted", end_reason="ui_shutdown", detail="UI shutdown requested.")
        self._finalize_session(status="interrupted", end_reason="ui_shutdown", detail="UI shutdown requested.")
        if self._yolo_warmup_future and not self._yolo_warmup_future.done():
            self._yolo_warmup_future.cancel()
        self._refresh_timer.stop()
        self._background_executor.shutdown(wait=False, cancel_futures=True)
