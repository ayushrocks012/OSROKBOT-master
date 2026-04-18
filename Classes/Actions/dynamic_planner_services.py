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
    OCRProvider,
    OCRRegionLike,
    WindowCaptureProvider,
)
from runtime_payloads import ResourceContext
from screen_change_detector import ScreenChangeDetector
from task_graph import TaskGraph
from vision_memory import VisionMemory

LOGGER = get_logger(__name__)


def _record_runtime_timing(
    context: Any,
    stage: str,
    started_at: float,
    *,
    detail: str = "",
) -> None:
    record_timing = getattr(context, "record_runtime_timing", None)
    if callable(record_timing):
        record_timing(stage, (time.perf_counter() - started_at) * 1000.0, detail=detail)


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
        self._state_monitor: Any | None = None

    def _get_state_monitor(self, context: Any) -> Any:
        if self._state_monitor is None:
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
        _record_runtime_timing(
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
        _record_runtime_timing(
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
        _record_runtime_timing(
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
        _record_runtime_timing(
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
            _record_runtime_timing(
                context,
                "resource_context",
                started_at,
                detail=f"keys={len(result)}",
            )
            return result if result else None
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            _record_runtime_timing(
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
    ) -> dict[str, Any] | None:
        """Block until the UI resolves a pending planner approval event."""

        pending = context.set_pending_planner_decision(
            decision,
            screenshot_path=screenshot_path,
            window_rect=window_rect,
            detections=detections,
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
            )
        finally:
            context.clear_pending_planner_decision()

        if not pending or pending.get("result") != "approved":
            LOGGER.warning("Dynamic planner action rejected by user.")
            return None, False

        corrected_point = pending.get("corrected_point")
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
        target_x, target_y = self._absolute_point(decision, window_rect)
        if not InputController.validate_bounds(target_x, target_y, window_rect):
            LOGGER.error("Dynamic planner target outside window: %s, %s", target_x, target_y)
            return False
        return bool(
            InputController(context=context).click(
                target_x,
                target_y,
                window_rect=window_rect,
                remember_position=False,
                context=context,
            )
        )

    def _execute_long_press(self, context: Any, decision: PlannerDecision, window_rect: ClientRectLike) -> bool:
        target_x, target_y = self._absolute_point(decision, window_rect)
        if not InputController.validate_bounds(target_x, target_y, window_rect):
            LOGGER.error("Dynamic planner long_press target outside window: %s, %s", target_x, target_y)
            return False
        return bool(
            InputController(context=context).long_press(
                target_x,
                target_y,
                window_rect=window_rect,
                remember_position=False,
                context=context,
            )
        )

    def _execute_drag(self, context: Any, decision: PlannerDecision, window_rect: ClientRectLike) -> bool:
        start_x, start_y = self._absolute_point(decision, window_rect)
        if not InputController.validate_bounds(start_x, start_y, window_rect):
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

        if not InputController.validate_bounds(end_x, end_y, window_rect):
            LOGGER.error("Dynamic planner drag end outside window: %s, %s", end_x, end_y)
            return False

        return bool(
            InputController(context=context).drag(
                start_x,
                start_y,
                end_x,
                end_y,
                window_rect=window_rect,
                context=context,
            )
        )

    @staticmethod
    def _execute_key(context: Any, decision: PlannerDecision, _window_rect: ClientRectLike) -> bool:
        controller = InputController(context=context)
        LOGGER.info("Dynamic planner key press: %s", decision.key_name)
        return bool(controller.key_press(decision.key_name, hold_seconds=0.1, context=context))

    @staticmethod
    def _execute_type(context: Any, decision: PlannerDecision, _window_rect: ClientRectLike) -> bool:
        controller = InputController(context=context)
        LOGGER.info("Dynamic planner typing: %s...", decision.text_content[:30])
        for char in decision.text_content:
            if not InputController.is_allowed(context):
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
        if decision.action_type == "wait":
            result = bool(self._delay_policy.wait(decision.delay_seconds, context=context))
            _record_runtime_timing(context, "planner_wait", started_at, detail=f"result={result}")
            return result
        if decision.action_type == "stop":
            if getattr(context, "bot", None):
                context.bot.stop()
            _record_runtime_timing(context, "planner_stop", started_at, detail="result=True")
            return False

        handlers = {
            "click": self._execute_click,
            "long_press": self._execute_long_press,
            "drag": self._execute_drag,
            "key": self._execute_key,
            "type": self._execute_type,
        }
        handler = handlers.get(decision.action_type)
        result = bool(handler(context, decision, window_rect)) if handler else False
        _record_runtime_timing(
            context,
            "input_execute",
            started_at,
            detail=f"action={decision.action_type} result={result}",
        )
        return result

    def wait_after_execution(self, delay_seconds: float, context: Any) -> bool:
        """Apply the planner-requested settle delay after a successful action."""

        started_at = time.perf_counter()
        result = bool(self._delay_policy.wait(delay_seconds, context=context))
        _record_runtime_timing(
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

    def ensure_task_graph(self, goal: str) -> None:
        """Build the mission task graph once per action instance."""

        if self._task_graph_initialized:
            return
        self.task_graph.decompose(goal, openai_client=self.planner.client, model=self.planner.model)
        self._task_graph_initialized = True

    def advance_progress(self, visible_labels: list[str], ocr_text: str) -> None:
        """Advance task-graph completion using current detector/OCR context."""

        self.task_graph.advance_if_completed(visible_labels, ocr_text)

    def mission_complete(self, context: Any) -> bool:
        """Stop the run when the task graph reports completion."""

        if not self.task_graph.is_complete():
            return False
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
        context.emit_state(f"Planner: {decision.action_type}\n{decision.label}")

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

        if correction:
            self.memory.record_correction(screenshot_path, decision, correction, visible_labels=visible_labels)
            self.dataset.export_correction(screenshot_path, decision, correction, detections=visible_labels)
            self._record_session_action(context, decision, "corrected")
            return

        self.memory.record_success(screenshot_path, decision, visible_labels=visible_labels, source=decision.source)
        self._record_session_action(context, decision, "success")
