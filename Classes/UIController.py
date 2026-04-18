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

from action_sets import ActionSets
from Actions.dynamic_planner_action import DynamicPlannerAction
from config_manager import ConfigManager
from context import Context
from detection_dataset import DetectionDataset
from input_controller import InputController
from logging_config import get_logger
from model_manager import ModelManager, yolo_download_required
from object_detector import create_detector
from ocr_service import OCRService
from OS_ROKBOT import OSROKBOT
from PyQt5 import QtCore, QtWidgets
from screen_change_detector import ScreenChangeDetector
from session_logger import SessionLogger
from task_graph import TaskGraph
from vision_memory import VisionMemory
from window_handler import WindowHandler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGGER = get_logger(__name__)
DEFAULT_MISSION = "Safely continue the selected Rise of Kingdoms task."
MIN_APPROVAL_CONFIDENCE = 0.70
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
    state_icon: str = "●"
    status_text: str = "Standing by"
    status_tone: str = "info"
    status_detail: str = ""
    mission_text: str = DEFAULT_MISSION
    mission_options: list[str] = field(default_factory=list)
    autonomy_level: int = 1
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
    total_seconds = max(0, int(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class UIController(QtCore.QObject):
    """Own automation-facing UI state and emit view-model snapshots."""

    snapshot_changed = QtCore.pyqtSignal(object)
    planner_overlay_requested = QtCore.pyqtSignal(dict)
    planner_overlay_cleared = QtCore.pyqtSignal()
    fix_overlay_requested = QtCore.pyqtSignal(dict)
    fix_overlay_cleared = QtCore.pyqtSignal()
    notification_requested = QtCore.pyqtSignal(str, str, object)

    def __init__(self, window_title: str, delay: float = 0.0, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.target_title = window_title
        self._window_handler = WindowHandler()
        self._input_controller = InputController(context=None)
        self._detector = create_detector()
        self._vision_memory = VisionMemory()
        self._detection_dataset = DetectionDataset()
        self.OS_ROKBOT = OSROKBOT(
            window_title,
            delay,
            window_handler=self._window_handler,
            input_controller=self._input_controller,
            detector=self._detector,
        )
        self.OS_ROKBOT.signal_emitter.pause_toggled.connect(self.handle_pause_toggled)
        self.OS_ROKBOT.signal_emitter.state_changed.connect(self.handle_runtime_state_changed)
        self.OS_ROKBOT.signal_emitter.planner_decision.connect(self.handle_planner_decision)
        self.OS_ROKBOT.signal_emitter.yolo_weights_ready.connect(self.handle_yolo_weights_ready)

        self.action_sets = ActionSets(
            OS_ROKBOT=self.OS_ROKBOT,
            dynamic_planner_factory=self._create_dynamic_planner_action,
        )
        self._background_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OSROKBOT-UI")
        self._yolo_warmup_future: Future[Any] | None = None
        self._current_context: Context | None = None
        self._session_logger: SessionLogger | None = None
        self._pending_payload: dict[str, Any] | None = None
        self._fix_capture_active = False
        self._session_active = False
        self._finalized_summary: dict[str, Any] | None = None
        self._finalized_timeline: list[dict[str, Any]] | None = None
        self._last_runtime_state = "Ready"
        self._status_text = "Standing by"
        self._status_tone = "info"
        self._status_detail = ""
        self._mission_text = DEFAULT_MISSION
        self._mission_options = self._load_mission_options()
        self._autonomy_level = self._load_autonomy_level()
        self._yolo_ready = True
        self._start_tooltip = "Start (F5)"

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.timeout.connect(self._emit_snapshot)
        self._refresh_timer.start(1000)

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

    def _load_mission_options(self) -> list[str]:
        config = ConfigManager()
        history: list[str] = []
        try:
            raw = config.get("MISSION_HISTORY", "")
            decoded = json.loads(raw) if raw else []
            if isinstance(decoded, list):
                history = [str(item).strip() for item in decoded if str(item).strip()]
        except Exception:
            history = []

        seen: set[str] = set()
        options: list[str] = []
        for item in history + MISSION_PRESETS:
            if item and item not in seen:
                seen.add(item)
                options.append(item)

        configured_goal = str(config.get("PLANNER_GOAL", "") or "").strip()
        self._mission_text = configured_goal or (options[0] if options else DEFAULT_MISSION)
        return options

    def _save_mission_to_history(self, mission: str) -> None:
        clean_mission = str(mission).strip()
        if not clean_mission:
            return

        try:
            raw = ConfigManager().get("MISSION_HISTORY", "")
            history = json.loads(raw) if raw else []
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

        filtered = [str(item).strip() for item in history if str(item).strip() and str(item).strip() != clean_mission]
        filtered.insert(0, clean_mission)
        ConfigManager().set_many({"MISSION_HISTORY": json.dumps(filtered[:MAX_MISSION_HISTORY])})
        self._mission_options = self._load_mission_options()

    @staticmethod
    def _load_autonomy_level() -> int:
        try:
            configured = int(ConfigManager().get("PLANNER_AUTONOMY_LEVEL", "1"))
        except (TypeError, ValueError):
            configured = 1
        return max(1, min(3, configured))

    def set_autonomy_level(self, level: int) -> None:
        """Update and persist the selected autonomy level."""

        self._autonomy_level = max(1, min(3, int(level)))
        ConfigManager().set_many({"PLANNER_AUTONOMY_LEVEL": str(self._autonomy_level)})
        self._emit_snapshot()

    def _create_dynamic_planner_action(self) -> DynamicPlannerAction:
        """Build one planner action with startup-owned shared services."""

        return DynamicPlannerAction(
            window_handler=self._window_handler,
            detector=self._detector,
            ocr=OCRService(),
            memory=self._vision_memory,
            dataset=self._detection_dataset,
            change_detector=ScreenChangeDetector(),
            task_graph=TaskGraph(),
        )

    def _set_status(self, text: str, tone: str, detail: str = "") -> None:
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
        self.begin_yolo_warmup()
        self._emit_snapshot()

    def start_automation(self, mission_text: str | None = None, autonomy_level: int | None = None) -> None:
        """Start a planner-first automation run from the selected mission."""

        mission = str(mission_text or self._mission_text or DEFAULT_MISSION).strip() or DEFAULT_MISSION
        selected_autonomy = max(1, min(3, int(autonomy_level or self._autonomy_level)))
        self._mission_text = mission
        self._autonomy_level = selected_autonomy

        if not self._yolo_ready:
            self._set_status("YOLO weights unavailable", "danger", ERROR_GUIDANCE["YOLO weights unavailable"])
            self._emit_snapshot()
            return
        if self.OS_ROKBOT.is_running or not self.OS_ROKBOT.all_threads_joined:
            self._set_status("Finishing previous run", "warning", "Wait for the current worker thread to join before restarting.")
            self._emit_snapshot()
            return
        if self.OS_ROKBOT.is_paused():
            self.OS_ROKBOT.toggle_pause()

        self._window_handler.activate_window(self.target_title)
        ConfigManager().set_many(
            {
                "PLANNER_GOAL": mission,
                "PLANNER_AUTONOMY_LEVEL": str(selected_autonomy),
            }
        )
        self._save_mission_to_history(mission)

        action_group = self.action_sets.dynamic_planner()
        if not action_group:
            self._set_status("Planner startup failed", "danger", "The dynamic planner action set could not be created.")
            self._emit_snapshot()
            return

        self._session_logger = SessionLogger(mission=mission, autonomy_level=selected_autonomy)
        context = Context(
            ui_instance=self,
            bot=self.OS_ROKBOT,
            signal_emitter=self.OS_ROKBOT.signal_emitter,
            window_title=self.target_title,
            session_logger=self._session_logger,
        )
        context.planner_goal = mission
        context.planner_autonomy_level = selected_autonomy
        self._current_context = context
        self._pending_payload = None
        self._fix_capture_active = False

        if self.OS_ROKBOT.start([action_group], context):
            self._session_logger.record_info(f"Session started: {mission}")
            self._session_active = True
            self._finalized_summary = None
            self._finalized_timeline = None
            self._last_runtime_state = "Running"
            self._set_status("Running", "success", "")
        else:
            self._set_status("Unable to start automation", "danger", "OSROKBOT refused to start the run.")
            self._current_context = None
        self._emit_snapshot()

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
        decision = pending.get("decision", {})
        action_type = str(decision.get("action_type", ""))
        try:
            confidence = float(decision.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return action_type in {"click", "drag", "long_press"} and (
            confidence < MIN_APPROVAL_CONFIDENCE or decision.get("source") == "ai_review"
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

    def stop_automation(self) -> None:
        """Stop the current automation run and finalize the active session log."""

        if self._current_context and self._current_context.pending_planner_decision():
            self._current_context.resolve_planner_decision(False)

        self.OS_ROKBOT.stop()
        self._pending_payload = None
        self._fix_capture_active = False
        self.planner_overlay_cleared.emit()
        self.fix_overlay_cleared.emit()

        if self._session_logger and self._session_active:
            self._session_logger.record_info("Session stopped.")
            self._finalized_summary = self._session_logger.summary()
            self._finalized_timeline = self._session_logger.timeline()
            path = self._session_logger.finalize()
            if path:
                self.notification_requested.emit(
                    "OSROKBOT - Session Saved",
                    f"Session log saved to {Path(path).name}",
                    QtWidgets.QSystemTrayIcon.Information,
                )
        self._session_active = False
        self._current_context = None
        self._last_runtime_state = "Ready"
        self._set_status("Ready", "info", "")
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
        lowered = text.lower()

        if "ai recovering" in lowered:
            self._set_status("AI recovering", "accent", "")
        elif "learning" in lowered:
            self._set_status("Learning from feedback", "success", "")
        elif "using memory" in lowered:
            self._set_status("Using trusted memory", "success", "")
        elif "captcha detected" in lowered:
            self._set_status("Captcha detected - paused", "warning", ERROR_GUIDANCE["Captcha detected"])
            if self._session_logger and "captcha detected" not in previous_state.lower():
                self._session_logger.record_captcha()
            if "captcha detected" not in previous_state.lower():
                self.notification_requested.emit(
                    "OSROKBOT - CAPTCHA",
                    "A CAPTCHA was detected. Solve it manually before resuming.",
                    QtWidgets.QSystemTrayIcon.Warning,
                )
        elif "game not foreground" in lowered:
            self._set_status("Game not foreground - paused", "warning", ERROR_GUIDANCE["Game not foreground"])
        elif "interception unavailable" in lowered:
            self._set_status("Interception unavailable", "danger", ERROR_GUIDANCE["Interception unavailable"])
        elif "planner approval needed" in lowered:
            self._set_status("Agent awaiting approval", "accent", "")
        elif "planner trusted" in lowered:
            self._set_status("Trusted action auto-approved", "success", "")
        elif "mission complete" in lowered:
            self._set_status("Mission complete", "success", "")
            if "mission complete" not in previous_state.lower():
                self.notification_requested.emit(
                    "OSROKBOT - Complete",
                    "The current mission has completed.",
                    QtWidgets.QSystemTrayIcon.Information,
                )
        elif self.OS_ROKBOT.is_running:
            self._set_status("Running", "success", "")
        else:
            self._set_status("Ready", "info", "")
        self._emit_snapshot()

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
            return "✓"
        if "captcha" in lowered or self.OS_ROKBOT.is_paused():
            return "⏸"
        if self._pending_payload:
            return "!"
        if "recover" in lowered:
            return "↺"
        if self.OS_ROKBOT.is_running:
            return "●"
        return "○"

    def _ui_mode(self) -> str:
        if self._pending_payload:
            return "approval"
        if self.OS_ROKBOT.is_running and not self.OS_ROKBOT.is_paused() and self._autonomy_level >= 2:
            return "compact"
        return "command"

    def _intent_state(self) -> IntentCardState:
        if not self._pending_payload:
            return IntentCardState()

        pending = self._pending_payload
        decision = pending.get("decision", {})
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        if confidence >= 0.85:
            confidence_tone = "success"
        elif confidence >= 0.70:
            confidence_tone = "warning"
        else:
            confidence_tone = "danger"

        fix_required = self._pending_requires_fix_payload(pending)
        absolute_x = pending.get("absolute_x")
        absolute_y = pending.get("absolute_y")
        return IntentCardState(
            visible=True,
            title="Fix required" if fix_required else "Awaiting approval",
            action_text=str(decision.get("action_type", "click") or "click").title(),
            target_text=f"{decision.get('label', 'target')} ({decision.get('target_id', 'manual') or 'manual'})",
            confidence=confidence,
            confidence_tone=confidence_tone,
            confidence_caption=f"{confidence:.0%} confidence",
            reason_text=str(decision.get("reason", "") or "No planner reason supplied."),
            coordinates_text=(
                f"Screen target: {int(absolute_x)}, {int(absolute_y)}"
                if absolute_x is not None and absolute_y is not None
                else "Screen target unavailable"
            ),
            shortcut_hint="OK disabled until Fix or No" if fix_required else "F8 OK   F9 No   F10 Fix",
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
        if not self._session_logger:
            return ["No session activity yet."]

        icon_map = {
            "action": "RUN",
            "approval": "OK",
            "rejection": "NO",
            "correction": "FIX",
            "error": "ERR",
            "captcha": "CAP",
            "planner_rejection": "DROP",
            "info": "INFO",
            "timing": "TIME",
        }
        lines: list[str] = []
        timeline = self._session_logger.timeline() if self._session_active or self._finalized_timeline is None else self._finalized_timeline
        for event in timeline:
            icon = icon_map.get(str(event.get("event_type", "")), "LOG")
            elapsed = f"+{float(event.get('elapsed_seconds', 0.0)):>4.0f}s"
            detail = str(event.get("label") or event.get("detail") or event.get("action_type") or "").strip()
            lines.append(f"{elapsed}  {icon:<4}  {detail or '(no detail)'}")
        return lines or ["No session activity yet."]

    def snapshot(self) -> SupervisorSnapshot:
        """Return the latest view-model snapshot."""

        if self._session_logger and self._session_active:
            elapsed = self._session_logger.duration_seconds()
        elif self._finalized_summary is not None:
            elapsed = float(self._finalized_summary.get("duration_seconds", 0.0))
        else:
            elapsed = 0.0
        is_running = bool(self.OS_ROKBOT.is_running)
        is_paused = bool(self.OS_ROKBOT.is_paused())
        can_start = self._yolo_ready and not is_running and bool(self.OS_ROKBOT.all_threads_joined)
        can_pause = is_running or is_paused
        can_stop = is_running or is_paused or self._current_context is not None
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

        self.stop_automation()
        if self._yolo_warmup_future and not self._yolo_warmup_future.done():
            self._yolo_warmup_future.cancel()
        self._refresh_timer.stop()
        self._background_executor.shutdown(wait=False, cancel_futures=True)
