"""Explicit runtime composition root for the supervisor console.

This module owns startup-time dependency construction for the supported PyQt
runtime so UIController can stay focused on session state, approval state, and
operator-facing view-model logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from action_sets import ActionSets
from Actions.dynamic_planner_action import DynamicPlannerAction
from ai_recovery_executor import AIRecoveryExecutor
from config_manager import ConfigManager
from context import Context
from detection_dataset import DetectionDataset
from gameplay_teaching import build_teaching_brief
from input_controller import InputController
from object_detector import create_detector
from ocr_service import OCRService
from OS_ROKBOT import OSROKBOT
from screen_change_detector import ScreenChangeDetector
from signal_emitter import SignalEmitter
from state_monitor import GameStateMonitor
from task_graph import TaskGraph
from vision_memory import VisionMemory
from window_handler import WindowHandler

DEFAULT_MISSION = "Safely continue the selected Rise of Kingdoms task."


@dataclass(slots=True)
class SupervisorRuntimeComposition:
    """Build the shared runtime collaborators for one UI process."""

    window_title: str
    delay: float = 0.0
    config: ConfigManager = field(default_factory=ConfigManager)
    signal_emitter: SignalEmitter = field(default_factory=SignalEmitter)
    window_handler: WindowHandler = field(default_factory=WindowHandler)
    detector: Any = field(default_factory=create_detector)
    vision_memory: VisionMemory = field(default_factory=VisionMemory)
    detection_dataset: DetectionDataset = field(default_factory=DetectionDataset)

    def build_bot(self) -> OSROKBOT:
        """Create the shared runner used by the supervisor console."""

        return OSROKBOT(
            self.window_title,
            self.delay,
            config=self.config,
            signal_emitter=self.signal_emitter,
            window_handler=self.window_handler,
            input_controller=InputController(context=None, window_handler=self.window_handler),
            detector=self.detector,
        )

    def create_action_sets(self, bot: OSROKBOT) -> ActionSets:
        """Create the supported action-set factory using shared services."""

        return ActionSets(
            OS_ROKBOT=bot,
            dynamic_planner_factory=self.create_dynamic_planner_action,
        )

    def create_dynamic_planner_action(self) -> DynamicPlannerAction:
        """Build one planner action using startup-owned shared collaborators."""

        return DynamicPlannerAction(
            window_handler=self.window_handler,
            detector=self.detector,
            ocr=OCRService(),
            memory=self.vision_memory,
            dataset=self.detection_dataset,
            change_detector=ScreenChangeDetector(),
            task_graph=TaskGraph(),
        )

    def create_input_controller(self, context: Context | None = None) -> InputController:
        """Create a context-bound guarded input controller."""

        return InputController(context=context, window_handler=self.window_handler)

    def create_state_monitor(self, context: Context | None = None) -> GameStateMonitor:
        """Create a state monitor that reuses the startup-owned collaborators."""

        return GameStateMonitor(
            context=context,
            config=self.config,
            window_handler=self.window_handler,
            input_controller=self.create_input_controller(context),
            detector=self.detector,
        )

    def create_recovery_executor(self, _context: Context | None = None) -> AIRecoveryExecutor:
        """Create the guarded recovery executor used by `StateMachine`."""

        return AIRecoveryExecutor(detector=self.detector)

    def create_context(
        self,
        *,
        bot: OSROKBOT,
        session_logger: Any | None = None,
        planner_goal: str = DEFAULT_MISSION,
        planner_autonomy_level: int = 1,
        teaching_mode_enabled: bool = False,
        teaching_profile_name: str = "guided_general",
        teaching_notes: str = "",
    ) -> Context:
        """Create one per-run runtime context wired to shared factories."""

        context = Context(
            bot=bot,
            signal_emitter=self.signal_emitter,
            window_title=self.window_title,
            session_logger=session_logger,
            window_handler_factory=lambda: self.window_handler,
            input_controller_factory=self.create_input_controller,
            state_monitor_factory=self.create_state_monitor,
            config_factory=lambda: self.config,
            recovery_executor_factory=self.create_recovery_executor,
        )
        context.planner_goal = planner_goal or DEFAULT_MISSION
        context.planner_autonomy_level = planner_autonomy_level
        context.teaching_mode_enabled = bool(teaching_mode_enabled)
        context.teaching_profile_name = str(teaching_profile_name or "guided_general")
        context.teaching_notes = str(teaching_notes or "").strip()
        context.teaching_brief = build_teaching_brief(
            enabled=context.teaching_mode_enabled,
            profile_name=context.teaching_profile_name,
            operator_notes=context.teaching_notes,
            mission=context.planner_goal,
        )
        return context
