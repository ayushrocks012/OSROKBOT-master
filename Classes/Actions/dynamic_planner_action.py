"""Guarded runtime bridge from side-effect-free planning to input execution."""

from __future__ import annotations

from typing import Any

from config_manager import ConfigManager
from detection_dataset import DetectionDataset
from dynamic_planner import DynamicPlanner
from logging_config import get_logger
from object_detector import create_detector
from ocr_service import OCRService
from runtime_contracts import DetectionProvider, OCRProvider, WindowCaptureProvider
from screen_change_detector import ScreenChangeDetector
from task_graph import TaskGraph
from vision_memory import VisionMemory
from window_handler import WindowHandler

from Actions.action import Action
from Actions.dynamic_planner_services import (
    PlannerApprovalService,
    PlannerExecutionService,
    PlannerFeedbackService,
    PlannerObservationService,
)

LOGGER = get_logger(__name__)


def _runtime_interrupted(context: Any) -> bool:
    bot = getattr(context, "bot", None) if context else None
    if not bot:
        return False
    stop_event = getattr(bot, "stop_event", None)
    pause_event = getattr(bot, "pause_event", None)
    return bool(
        (stop_event is not None and stop_event.is_set())
        or (pause_event is not None and pause_event.is_set())
    )


def _emit_planner_trace(
    context: Any,
    *,
    decision: Any,
    focused_goal: str,
    visible_labels: list[str],
    ocr_text: str,
    screen_changed: bool,
    stuck_warning: str,
) -> None:
    """Send the latest planner observation and decision summary to the UI."""

    emit_trace = getattr(context, "emit_planner_trace", None)
    if not callable(emit_trace):
        return
    decision_payload = decision.to_dict() if hasattr(decision, "to_dict") else decision
    emit_trace(
        {
            "focused_goal": focused_goal,
            "visible_labels": list(visible_labels),
            "ocr_text": str(ocr_text or ""),
            "screen_changed": bool(screen_changed),
            "stuck_warning": str(stuck_warning or ""),
            "decision": dict(decision_payload) if isinstance(decision_payload, dict) else {},
        }
    )


class DynamicPlannerAction(Action):
    """Execute one guarded planner step using explicit helper services."""

    def __init__(
        self,
        goal: str | None = None,
        delay: float = 0,
        post_delay: float = 0.5,
        *,
        window_handler: WindowCaptureProvider | None = None,
        detector: DetectionProvider | None = None,
        ocr: OCRProvider | None = None,
        memory: VisionMemory | None = None,
        planner: DynamicPlanner | None = None,
        dataset: DetectionDataset | None = None,
        change_detector: ScreenChangeDetector | None = None,
        task_graph: TaskGraph | None = None,
        observation_service: PlannerObservationService | None = None,
        approval_service: PlannerApprovalService | None = None,
        execution_service: PlannerExecutionService | None = None,
        feedback_service: PlannerFeedbackService | None = None,
    ) -> None:
        super().__init__(delay=delay, post_delay=post_delay)
        self.goal = goal
        self.window_handler: WindowCaptureProvider = window_handler or WindowHandler()
        self.detector: DetectionProvider = detector or create_detector()
        self.ocr: OCRProvider = ocr or OCRService()
        self.memory = memory or VisionMemory()
        self.planner = planner or DynamicPlanner(memory=self.memory)
        self.dataset = dataset or DetectionDataset()
        self.change_detector = change_detector or ScreenChangeDetector()
        self.task_graph = task_graph or TaskGraph()
        self.observation_service = observation_service or PlannerObservationService(
            window_handler=self.window_handler,
            detector=self.detector,
            ocr=self.ocr,
            change_detector=self.change_detector,
        )
        self.approval_service = approval_service or PlannerApprovalService(memory=self.memory)
        self.execution_service = execution_service or PlannerExecutionService()
        self.feedback_service = feedback_service or PlannerFeedbackService(
            task_graph=self.task_graph,
            planner=self.planner,
            memory=self.memory,
            dataset=self.dataset,
            change_detector=self.change_detector,
        )

    def close(self) -> None:
        """Release planner resources owned by this action."""

        self.planner.close()

    @property
    def status_text(self) -> str:
        return "DynamicPlanner\nAI guarded step"

    def _planner_goal(self, context: Any) -> str:
        return self.goal or getattr(context, "planner_goal", None) or ConfigManager().get(
            "PLANNER_GOAL",
            "Safely continue the selected Rise of Kingdoms task.",
        )

    def execute(self, context: Any | None = None) -> bool:
        if not context:
            return False

        goal = self._planner_goal(context)
        context.emit_state("DynamicPlanner\nobserving")
        observation = self.observation_service.observe(context)
        if observation is None:
            return False

        screenshot_path = self.observation_service.save_latest_screenshot(self.planner.memory.path, observation.screenshot)
        screen_changed, stuck_warning = self.observation_service.screen_change_context(observation.screenshot)
        if _runtime_interrupted(context):
            return False
        self.feedback_service.ensure_task_graph(context, goal)
        if _runtime_interrupted(context):
            return False

        ocr_text, ocr_regions = self.observation_service.read_planner_ocr(context, observation.screenshot)
        if _runtime_interrupted(context):
            return False
        resource_context = self.observation_service.read_resource_context(context)
        if _runtime_interrupted(context):
            return False
        visible_labels = self.observation_service.visible_labels(observation.detections)
        focused_goal = self.feedback_service.focused_goal(goal)
        record_no_progress_feedback = getattr(self.feedback_service, "record_no_progress_feedback", None)
        if callable(record_no_progress_feedback):
            record_no_progress_feedback(
                context,
                goal=focused_goal,
                visible_labels=visible_labels,
                ocr_text=ocr_text,
            )
        self.feedback_service.advance_progress(visible_labels, ocr_text, context=context)

        if self.feedback_service.mission_complete(context):
            return False

        decision = self.planner.plan_next(
            context,
            screenshot_path,
            observation.detections,
            ocr_text,
            focused_goal,
            ocr_regions=ocr_regions,
            resource_context=resource_context,
            stuck_warning=stuck_warning,
            screen_changed=screen_changed,
        )
        if not decision:
            self.feedback_service.record_no_decision(screenshot_path, observation.detections)
            return False

        self.feedback_service.record_decision(context, decision)
        _emit_planner_trace(
            context,
            decision=decision,
            focused_goal=focused_goal,
            visible_labels=visible_labels,
            ocr_text=ocr_text,
            screen_changed=screen_changed,
            stuck_warning=stuck_warning,
        )
        if decision.action_type == "wait":
            self.feedback_service.record_wait(context, screenshot_path, decision, observation.detections)
            return self.execution_service.execute(context, decision, observation.window_rect)
        if decision.action_type == "stop":
            return self.execution_service.execute(context, decision, observation.window_rect)

        approved_decision, corrected = self.approval_service.approve_pointer_decision(
            context,
            decision,
            screenshot_path,
            observation.window_rect,
            detections=observation.detections,
            sub_goal=focused_goal,
        )
        if approved_decision is None:
            self.feedback_service.record_failure(context, decision)
            return False

        executed = self.execution_service.execute(context, approved_decision, observation.window_rect)
        if not executed:
            self.feedback_service.record_failure(context, approved_decision)
            return False
        if not self.execution_service.wait_after_execution(approved_decision.delay_seconds, context):
            return False

        correction = {"x": approved_decision.x, "y": approved_decision.y} if corrected else None
        self.feedback_service.record_success(
            context,
            screenshot_path,
            approved_decision,
            correction,
            observation.detections,
        )
        return True
