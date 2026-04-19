"""Service helpers used by the guarded dynamic planner action.

These services isolate observation building, human approval, task-graph and
memory feedback, and input execution so `DynamicPlannerAction` can stay focused
on orchestrating one planner step.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config_manager import ConfigManager
from detection_dataset import DetectionDataset
from dynamic_planner import MIN_PLANNER_CONFIDENCE, DynamicPlanner, PlannerDecision
from input_controller import DelayPolicy, InputController
from logging_config import get_logger
from PIL import Image
from runtime_contracts import (
    ClientRectLike,
    DetectionLike,
    DetectionProvider,
    InputControllerLike,
    OCRProvider,
    OCRRegionLike,
    StateMonitorLike,
    WindowCaptureProvider,
)
from runtime_payloads import ResourceContext
from screen_change_detector import ScreenChangeDetector
from task_graph import TaskGraph
from vision_memory import VisionMemory
from context import record_stage_timing

LOGGER = get_logger(__name__)




def _session_logger(context: Any) -> Any | None:
    return getattr(context, "session_logger", None)


def _step_scope(context: Any) -> dict[str, Any]:
    active_step_scope = getattr(context, "active_step_scope", None)
    if callable(active_step_scope):
        scope = active_step_scope()
        if isinstance(scope, dict):
            return {str(key): value for key, value in scope.items()}
    return {}


def _step_identity(context: Any) -> tuple[str, str, str]:
    scope = _step_scope(context)
    return (
        str(scope.get("step_id", "") or ""),
        str(scope.get("machine_id", "") or ""),
        str(scope.get("state_name", "") or ""),
    )


def _update_step_scope(
    context: Any,
    *,
    decision_id: str | None = None,
    approval_id: str | None = None,
    input_id: str | None = None,
) -> None:
    update_active_step_scope = getattr(context, "update_active_step_scope", None)
    if callable(update_active_step_scope):
        update_active_step_scope(
            decision_id=decision_id,
            approval_id=approval_id,
            input_id=input_id,
        )


@dataclass(frozen=True)
class PlannerObservation:
    """Screenshot observation passed into one planner step."""

    screenshot: Image.Image
    window_rect: ClientRectLike
    detections: list[DetectionLike]


class PlannerObservationService:
    """Capture and enrich the planner's current screen observation."""

    def __init__(
        self,
        *,
        window_handler: WindowCaptureProvider,
        detector: DetectionProvider,
        ocr: OCRProvider,
        change_detector: ScreenChangeDetector,
    ) -> None:
        self.window_handler = window_handler
        self.detector = detector
        self.ocr = ocr
        self.change_detector = change_detector
        self._state_monitor: StateMonitorLike | None = None

    def _get_state_monitor(self, context: Any) -> StateMonitorLike:
        if self._state_monitor is None:
            build_state_monitor = getattr(context, "build_state_monitor", None)
            if callable(build_state_monitor):
                self._state_monitor = build_state_monitor()
            else:
                from state_monitor import GameStateMonitor

                self._state_monitor = GameStateMonitor(context=context)
        return self._state_monitor

    def observe(self, context: Any) -> PlannerObservation | None:
        """Reuse the shared observation when possible, else capture locally."""

        observation = context.get_current_observation() if hasattr(context, "get_current_observation") else getattr(context, "current_observation", None)
        if observation and getattr(observation, "screenshot", None) is not None and getattr(observation, "window_rect", None) is not None:
            detections = list(getattr(observation, "detections", ()))
            LOGGER.debug("Dynamic planner reused shared observation detections=%s", len(detections))
            return PlannerObservation(observation.screenshot, observation.window_rect, detections)

        capture_started_at = time.perf_counter()
        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        record_stage_timing(
            context,
            "window_capture",
            capture_started_at,
            detail=f"title={context.window_title}",
        )
        if screenshot is None or window_rect is None:
            return None

        detect_started_at = time.perf_counter()
        try:
            detections = list(self.detector.detect(screenshot))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Dynamic planner local detector skipped: %s", exc)
            detections = []
        record_stage_timing(
            context,
            "yolo_detect",
            detect_started_at,
            detail=f"detections={len(detections)}",
        )
        LOGGER.debug(
            "Dynamic planner local YOLO duration_ms=%.2f detections=%s",
            (time.perf_counter() - detect_started_at) * 1000.0,
            len(detections),
        )
        return PlannerObservation(screenshot, window_rect, detections)

    @staticmethod
    def save_latest_screenshot(memory_path: Path, screenshot: Image.Image) -> Path:
        """Persist the latest planner screenshot next to visual memory."""

        screenshot_path = memory_path.parent / "planner_latest.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot.save(screenshot_path)
        return screenshot_path

    def screen_change_context(self, screenshot: Image.Image) -> tuple[bool, str]:
        """Return screen-change and repeated-action context for the planner."""

        self.change_detector.record_screenshot(screenshot)
        return self.change_detector.screen_changed_since_last(), self.change_detector.stuck_warning_text()

    def read_planner_ocr(self, context: Any, screenshot: Image.Image) -> tuple[str, list[OCRRegionLike]]:
        """Read OCR regions first, then fall back to plain OCR text."""

        ocr_started_at = time.perf_counter()
        ocr_regions = list(self.ocr.read_regions(screenshot, purpose="planner"))
        record_stage_timing(
            context,
            "ocr_regions",
            ocr_started_at,
            detail=f"regions={len(ocr_regions)}",
        )
        LOGGER.debug(
            "Dynamic planner OCR regions duration_ms=%.2f regions=%s",
            (time.perf_counter() - ocr_started_at) * 1000.0,
            len(ocr_regions),
        )

        ocr_text = " ".join(region.text for region in ocr_regions if getattr(region, "text", "")).strip()
        if ocr_text:
            return ocr_text, ocr_regions

        ocr_text_started_at = time.perf_counter()
        ocr_text = self.ocr.read(screenshot, purpose="planner")
        record_stage_timing(
            context,
            "ocr_text",
            ocr_text_started_at,
            detail=f"text_len={len(ocr_text)}",
        )
        LOGGER.debug(
            "Dynamic planner OCR text duration_ms=%.2f text_len=%s",
            (time.perf_counter() - ocr_text_started_at) * 1000.0,
            len(ocr_text),
        )
        return ocr_text, ocr_regions

    def read_resource_context(self, context: Any) -> ResourceContext | None:
        """Read march-slot and action-point context for the planner."""

        started_at = time.perf_counter()
        try:
            monitor = self._get_state_monitor(context)
            march_slots = monitor.count_idle_march_slots()
            action_points = monitor.read_action_points()
            result: ResourceContext = {}
            if march_slots is not None:
                result["idle_march_slots"] = march_slots
            if action_points is not None:
                result["action_points"] = action_points
            record_stage_timing(
                context,
                "resource_context",
                started_at,
                detail=f"keys={len(result)}",
            )
            return result if result else None
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            record_stage_timing(
                context,
                "resource_context",
                started_at,
                detail=f"error={type(exc).__name__}",
            )
            LOGGER.warning("Resource context read failed: %s", exc)
            return None

    @staticmethod
    def visible_labels(detections: list[DetectionLike]) -> list[str]:
        """Return visible detector labels as strings."""

        return [str(getattr(detection, "label", "")) for detection in detections if getattr(detection, "label", "")]


class PlannerApprovalService:
    """Handle autonomy-gated approval for pointer-based planner decisions."""

    def __init__(self, *, memory: VisionMemory, config: ConfigManager | None = None) -> None:
        self.memory = memory
        self.config = config or ConfigManager()

    @staticmethod
    def _autonomy_level(context: Any) -> int:
        try:
            return int(getattr(context, "planner_autonomy_level", 1))
        except (TypeError, ValueError):
            return 1

    def _trusted_count(self) -> int:
        try:
            return int(self.config.get("PLANNER_TRUSTED_SUCCESS_COUNT", "3"))
        except (TypeError, ValueError):
            return 3

    def wait_for_approval(
        self,
        context: Any,
        decision: PlannerDecision,
        screenshot_path: Path,
        window_rect: ClientRectLike,
        detections: list[DetectionLike] | None = None,
        sub_goal: str = "",
    ) -> dict[str, Any] | None:
        """Block until the UI resolves a pending planner approval event."""

        session_logger = _session_logger(context)
        step_id, machine_id, state_name = _step_identity(context)
        decision_id = str(_step_scope(context).get("decision_id", "") or "")
        fix_required = decision.source == "ai_review" or decision.confidence < MIN_PLANNER_CONFIDENCE
        if (
            session_logger
            and step_id
            and decision_id
            and hasattr(session_logger, "record_approval_requested")
        ):
            approval_id = session_logger.record_approval_requested(
                step_id=step_id,
                machine_id=machine_id or "machine_unknown",
                state_name=state_name,
                decision_id=decision_id,
                action_type=decision.action_type,
                label=decision.label,
                target_id=decision.target_id,
                fix_required=fix_required,
            )
            _update_step_scope(context, approval_id=approval_id, input_id=None)

        pending = context.set_pending_planner_decision(
            decision,
            screenshot_path=screenshot_path,
            window_rect=window_rect,
            detections=detections,
            sub_goal=sub_goal,
        )
        event = pending.get("event")
        delay_policy = DelayPolicy()
        while event and not event.is_set():
            if not InputController.is_allowed(context):
                return None
            if not delay_policy.wait(0.1, context=context):
                return None
        return pending

    def approve_pointer_decision(
        self,
        context: Any,
        decision: PlannerDecision,
        screenshot_path: Path,
        window_rect: ClientRectLike,
        detections: list[DetectionLike] | None = None,
        sub_goal: str = "",
    ) -> tuple[PlannerDecision | None, bool]:
        """Return the approved decision and whether a manual correction was applied."""

        if decision.action_type not in {"click", "drag", "long_press"}:
            return decision, False

        autonomy = self._autonomy_level(context)
        if autonomy >= 3:
            return decision, False
        if autonomy == 2 and self.memory.is_trusted_label(decision.label, min_success=self._trusted_count()):
            context.emit_state("Planner trusted auto-click")
            return decision, False

        try:
            pending = self.wait_for_approval(
                context,
                decision,
                screenshot_path,
                window_rect,
                detections=detections,
                sub_goal=sub_goal,
            )
        finally:
            context.clear_pending_planner_decision()

        session_logger = _session_logger(context)
        scope = _step_scope(context)
        step_id = str(scope.get("step_id", "") or "")
        machine_id = str(scope.get("machine_id", "") or "machine_unknown")
        state_name = str(scope.get("state_name", "") or "")
        decision_id = str(scope.get("decision_id", "") or "")
        approval_id = str(scope.get("approval_id", "") or "")
        corrected_point = pending.get("corrected_point") if pending else None
        if pending is None:
            approval_outcome = "interrupted"
        elif pending.get("result") != "approved":
            approval_outcome = "rejected"
        elif corrected_point:
            approval_outcome = "corrected"
        else:
            approval_outcome = "approved"
        if (
            session_logger
            and step_id
            and decision_id
            and approval_id
            and hasattr(session_logger, "record_approval_resolved")
        ):
            session_logger.record_approval_resolved(
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                decision_id=decision_id,
                approval_id=approval_id,
                outcome=approval_outcome,
                corrected_point=corrected_point,
            )
        _update_step_scope(context, approval_id=None)

        if not pending or pending.get("result") != "approved":
            LOGGER.warning("Dynamic planner action rejected by user.")
            return None, False

        if corrected_point:
            corrected = decision.model_copy(
                update={
                    "x": float(corrected_point["x"]),
                    "y": float(corrected_point["y"]),
                    "confidence": 1.0,
                    "source": "manual",
                }
            )
            return corrected, True
        if decision.source == "ai_review" or decision.confidence < MIN_PLANNER_CONFIDENCE:
            LOGGER.warning("Low-confidence planner action requires Fix before execution.")
            return None, False
        return decision, False


class PlannerExecutionService:
    """Execute a validated planner decision through InputController."""

    def __init__(self) -> None:
        self._delay_policy = DelayPolicy()
        self._controller: InputControllerLike | None = None

    def _input_controller(self, context: Any) -> InputControllerLike:
        if self._controller is not None:
            return self._controller
        build_input_controller = getattr(context, "build_input_controller", None)
        if callable(build_input_controller):
            self._controller = build_input_controller()
        else:
            self._controller = InputController(context=context)
        return self._controller

    @staticmethod
    def _validate_bounds(controller: InputControllerLike, x: int, y: int, window_rect: ClientRectLike) -> bool:
        validator = getattr(controller, "validate_bounds", None)
        if callable(validator):
            return bool(validator(x, y, window_rect))
        return bool(InputController.validate_bounds(x, y, window_rect))

    @staticmethod
    def _is_allowed(controller: InputControllerLike, context: Any) -> bool:
        is_allowed = getattr(controller, "is_allowed", None)
        if callable(is_allowed):
            return bool(is_allowed(context))
        return bool(InputController.is_allowed(context))

    @staticmethod
    def _absolute_point(decision: PlannerDecision, window_rect: ClientRectLike) -> tuple[int, int]:
        return (
            int(round(window_rect.left + window_rect.width * decision.x)),
            int(round(window_rect.top + window_rect.height * decision.y)),
        )

    @staticmethod
    def _absolute_end_point(decision: PlannerDecision, window_rect: ClientRectLike) -> tuple[int, int]:
        return (
            int(round(window_rect.left + window_rect.width * decision.end_x)),
            int(round(window_rect.top + window_rect.height * decision.end_y)),
        )

    def _execute_click(self, context: Any, decision: PlannerDecision, window_rect: ClientRectLike) -> bool:
        controller = self._input_controller(context)
        target_x, target_y = self._absolute_point(decision, window_rect)
        if not self._validate_bounds(controller, target_x, target_y, window_rect):
            LOGGER.error("Dynamic planner target outside window: %s, %s", target_x, target_y)
            return False
        return bool(
            controller.click(
                target_x,
                target_y,
                window_rect=window_rect,
                remember_position=False,
                context=context,
            )
        )

    def _execute_long_press(self, context: Any, decision: PlannerDecision, window_rect: ClientRectLike) -> bool:
        controller = self._input_controller(context)
        target_x, target_y = self._absolute_point(decision, window_rect)
        if not self._validate_bounds(controller, target_x, target_y, window_rect):
            LOGGER.error("Dynamic planner long_press target outside window: %s, %s", target_x, target_y)
            return False
        return bool(
            controller.long_press(
                target_x,
                target_y,
                window_rect=window_rect,
                remember_position=False,
                context=context,
            )
        )

    def _execute_drag(self, context: Any, decision: PlannerDecision, window_rect: ClientRectLike) -> bool:
        controller = self._input_controller(context)
        start_x, start_y = self._absolute_point(decision, window_rect)
        if not self._validate_bounds(controller, start_x, start_y, window_rect):
            LOGGER.error("Dynamic planner drag start outside window: %s, %s", start_x, start_y)
            return False

        if math.isfinite(decision.end_x) and math.isfinite(decision.end_y):
            end_x, end_y = self._absolute_end_point(decision, window_rect)
        elif decision.drag_direction:
            drag_dx = int(window_rect.width * 0.3)
            drag_dy = int(window_rect.height * 0.3)
            direction = decision.drag_direction.lower()
            end_x = start_x - drag_dx if "left" in direction else start_x + drag_dx if "right" in direction else start_x
            end_y = start_y - drag_dy if "up" in direction else start_y + drag_dy if "down" in direction else start_y
        else:
            LOGGER.error("Dynamic planner drag has no end target or direction.")
            return False

        if not self._validate_bounds(controller, end_x, end_y, window_rect):
            LOGGER.error("Dynamic planner drag end outside window: %s, %s", end_x, end_y)
            return False

        return bool(
            controller.drag(
                start_x,
                start_y,
                end_x,
                end_y,
                window_rect=window_rect,
                context=context,
            )
        )

    def _execute_key(self, context: Any, decision: PlannerDecision, _window_rect: ClientRectLike) -> bool:
        controller = self._input_controller(context)
        if not self._is_allowed(controller, context):
            return False
        LOGGER.info("Dynamic planner key press: %s", decision.key_name)
        return bool(controller.key_press(decision.key_name, hold_seconds=0.1, context=context))

    def _execute_type(self, context: Any, decision: PlannerDecision, _window_rect: ClientRectLike) -> bool:
        controller = self._input_controller(context)
        LOGGER.info("Dynamic planner typing: %s...", decision.text_content[:30])
        if not self._is_allowed(controller, context):
            return False
        for char in decision.text_content:
            if not self._is_allowed(controller, context):
                return False
            if not controller.key_press(char, hold_seconds=0.05, context=context):
                return False
        return True

    def execute(
        self,
        context: Any,
        decision: PlannerDecision,
        window_rect: ClientRectLike,
    ) -> bool:
        """Dispatch the decision through the correct guarded input path."""

        started_at = time.perf_counter()
        session_logger = _session_logger(context)
        scope = _step_scope(context)
        step_id = str(scope.get("step_id", "") or "")
        machine_id = str(scope.get("machine_id", "") or "machine_unknown")
        state_name = str(scope.get("state_name", "") or "")
        decision_id = str(scope.get("decision_id", "") or "")
        input_id = ""
        if (
            decision.action_type not in {"wait", "stop"}
            and session_logger
            and step_id
            and decision_id
            and hasattr(session_logger, "record_input_started")
        ):
            input_id = session_logger.record_input_started(
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                decision_id=decision_id,
                action_type=decision.action_type,
                label=decision.label,
                target_id=decision.target_id,
            )
            _update_step_scope(context, input_id=input_id)
        if decision.action_type == "wait":
            result = bool(self._delay_policy.wait(decision.delay_seconds, context=context))
            record_stage_timing(context, "planner_wait", started_at, detail=f"result={result}")
            return result
        if decision.action_type == "stop":
            session_logger = getattr(context, "session_logger", None)
            if session_logger and hasattr(session_logger, "mark_terminal"):
                session_logger.mark_terminal("success", "planner_stop", detail="Planner chose a stop action.")
            if getattr(context, "bot", None):
                context.bot.stop()
            record_stage_timing(context, "planner_stop", started_at, detail="result=True")
            return False

        handlers = {
            "click": self._execute_click,
            "long_press": self._execute_long_press,
            "drag": self._execute_drag,
            "key": self._execute_key,
            "type": self._execute_type,
        }
        handler = handlers.get(decision.action_type)
        try:
            result = bool(handler(context, decision, window_rect)) if handler else False
        except Exception as exc:
            if (
                input_id
                and session_logger
                and hasattr(session_logger, "record_input_completed")
            ):
                session_logger.record_input_completed(
                    step_id=step_id,
                    machine_id=machine_id,
                    state_name=state_name,
                    decision_id=decision_id,
                    input_id=input_id,
                    action_type=decision.action_type,
                    outcome="exception",
                    label=decision.label,
                    target_id=decision.target_id,
                    detail=str(exc),
                )
            _update_step_scope(context, input_id=None)
            raise
        record_stage_timing(
            context,
            "input_execute",
            started_at,
            detail=f"action={decision.action_type} result={result}",
        )
        if (
            input_id
            and session_logger
            and hasattr(session_logger, "record_input_completed")
        ):
            session_logger.record_input_completed(
                step_id=step_id,
                machine_id=machine_id,
                state_name=state_name,
                decision_id=decision_id,
                input_id=input_id,
                action_type=decision.action_type,
                outcome="success" if result else "failure",
                label=decision.label,
                target_id=decision.target_id,
            )
        _update_step_scope(context, input_id=None)
        if not result:
            if session_logger and hasattr(session_logger, "record_error"):
                session_logger.record_error(
                    f"Guarded input failed for {decision.action_type}.",
                    stage="input_execute",
                    action_type=decision.action_type,
                    label=decision.label,
                    target_id=decision.target_id,
                    outcome="failure",
                )
        return result

    def wait_after_execution(self, delay_seconds: float, context: Any) -> bool:
        """Apply the planner-requested settle delay after a successful action."""

        started_at = time.perf_counter()
        result = bool(self._delay_policy.wait(delay_seconds, context=context))
        record_stage_timing(
            context,
            "post_action_wait",
            started_at,
            detail=f"result={result}",
        )
        return result


class PlannerFeedbackService:
    """Own task-graph progress, memory updates, and artifact feedback."""

    def __init__(
        self,
        *,
        task_graph: TaskGraph,
        planner: DynamicPlanner,
        memory: VisionMemory,
        dataset: DetectionDataset,
        change_detector: ScreenChangeDetector,
    ) -> None:
        self.task_graph = task_graph
        self.planner = planner
        self.memory = memory
        self.dataset = dataset
        self.change_detector = change_detector
        self._task_graph_initialized = False

    def ensure_task_graph(self, context: Any, goal: str) -> None:
        """Build the mission task graph once per action instance."""

        if self._task_graph_initialized:
            return
        self.task_graph.decompose(
            goal,
            transport=self.planner.transport,
            model=self.planner.model,
            should_cancel=lambda: self.planner.request_interrupted(context),
            context=context,
        )
        self._task_graph_initialized = True

    def advance_progress(self, visible_labels: list[str], ocr_text: str, context: Any = None) -> None:
        """Advance task-graph completion using current detector/OCR context."""

        if self.task_graph.advance_if_completed(visible_labels, ocr_text):
            if context:
                context.emit_state(self.task_graph.progress_summary())

    def mission_complete(self, context: Any) -> bool:
        """Stop the run when the task graph reports completion."""

        if not self.task_graph.is_complete():
            return False
        session_logger = getattr(context, "session_logger", None)
        if session_logger and hasattr(session_logger, "mark_terminal"):
            session_logger.mark_terminal("success", "mission_complete", detail="Task graph reported mission completion.")
        context.emit_state("Mission complete")
        LOGGER.info("TaskGraph reports all sub-goals completed.")
        if getattr(context, "bot", None):
            context.bot.stop()
        return True

    def focused_goal(self, goal: str) -> str:
        """Return the current focused sub-goal for the planner."""

        return self.task_graph.focused_goal_text(goal)

    def record_decision(self, context: Any, decision: PlannerDecision) -> None:
        """Record the last planner decision for stuck-screen warnings and UI state."""

        self.change_detector.record_action(
            decision.action_type,
            target_id=decision.target_id,
            label=decision.label,
        )
        context.extracted["planner_last_decision"] = decision.to_dict()
        session_logger = _session_logger(context)
        if session_logger and hasattr(session_logger, "record_decision"):
            session_logger.record_decision(decision.to_dict())
        step_id, machine_id, state_name = _step_identity(context)
        if (
            session_logger
            and step_id
            and hasattr(session_logger, "record_decision_selected")
        ):
            decision_id = session_logger.record_decision_selected(
                step_id=step_id,
                machine_id=machine_id or "machine_unknown",
                state_name=state_name,
                action_type=decision.action_type,
                label=decision.label,
                target_id=decision.target_id,
                source=decision.source,
                confidence=float(decision.confidence),
            )
            _update_step_scope(context, decision_id=decision_id, approval_id=None, input_id=None)
        context.emit_state(
            f"Planner: {decision.action_type} → {decision.label}\n"
            f"Reason: {decision.reason}\n"
            f"Confidence: {decision.confidence:.0%}"
        )

    def record_no_decision(self, screenshot_path: Path, detections: list[DetectionLike]) -> None:
        """Export a recovery dataset stub when the planner cannot decide."""

        self.dataset.export_stub(screenshot_path, "planner_no_decision", detections=detections)

    @staticmethod
    def _record_session_action(context: Any, decision: PlannerDecision, outcome: str) -> None:
        session_logger = getattr(context, "session_logger", None)
        if session_logger:
            session_logger.record_action(
                decision.action_type,
                label=decision.label,
                target_id=decision.target_id,
                outcome=outcome,
                source=decision.source,
            )

    def record_wait(self, context: Any, screenshot_path: Path, decision: PlannerDecision, visible_labels: list[Any]) -> None:
        """Record a successful wait decision as a session event.

        Wait decisions are not useful visual memories and can otherwise trigger
        optional CLIP/Torch startup on workstations that only need OCR/planner
        operation.
        """

        _ = screenshot_path, visible_labels
        self._record_session_action(context, decision, "success")

    def record_failure(self, context: Any, decision: PlannerDecision) -> None:
        """Penalize a failed decision and write a failed session action event."""

        self.memory.record_failure(decision.to_dict())
        self._record_session_action(context, decision, "failure")

    def record_success(
        self,
        context: Any,
        screenshot_path: Path,
        decision: PlannerDecision,
        correction: dict[str, float] | None,
        visible_labels: list[Any],
    ) -> None:
        """Persist either a corrected or normal successful planner action."""

        mission = getattr(self.task_graph, "mission", "")
        if correction:
            self.memory.record_correction(screenshot_path, decision, correction, visible_labels=visible_labels, mission=mission)
            self.dataset.export_correction(screenshot_path, decision, correction, detections=visible_labels)
            self._record_session_action(context, decision, "corrected")
            return

        self.memory.record_success(screenshot_path, decision, visible_labels=visible_labels, source=decision.source, mission=mission)
        self._record_session_action(context, decision, "success")
