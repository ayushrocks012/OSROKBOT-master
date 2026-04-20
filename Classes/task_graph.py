"""Multi-step goal decomposition for complex missions.

This module owns the planner-adjacent mission-decomposition boundary. It
decomposes a natural-language mission into ordered sub-goals using the shared
planner transport, then feeds each sub-goal as focused context to the planner
loop. Sub-goal completion is detected by matching expected post-conditions
against the currently visible labels and OCR text.

Side Effects:
    May call the shared planner transport for one OpenAI Responses request.
    Does not execute input or mutate game state.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from config_manager import ConfigManager
from encoding_utils import safe_json_loads
from logging_config import get_logger
from runtime_contracts import PlannerTransport

LOGGER = get_logger(__name__)


DECOMPOSITION_SCHEMA = {
    "type": "object",
    "properties": {
        "sub_goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "description": {"type": "string"},
                    "expected_labels": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "expected_ocr_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "completion_hint": {"type": "string"},
                },
                "required": [
                    "step",
                    "description",
                    "expected_labels",
                    "expected_ocr_keywords",
                    "completion_hint",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sub_goals"],
    "additionalProperties": False,
}


@dataclass
class SubGoal:
    """One step in a decomposed mission."""

    step: int
    description: str
    expected_labels: list[str] = field(default_factory=list)
    expected_ocr_keywords: list[str] = field(default_factory=list)
    completion_hint: str = ""
    completed: bool = False

    def is_completed_by(
        self,
        visible_labels: list[str] | None = None,
        ocr_text: str = "",
    ) -> bool:
        """Check if current observations satisfy this sub-goal's post-conditions.

        When detector labels are configured for the step, they are treated as
        the authoritative completion signal. OCR keywords are used only for
        OCR-only steps without expected labels. This intentionally biases the
        graph toward false negatives instead of unsafe false positives when
        detector coverage is temporarily missing.
        """
        if not self.expected_labels and not self.expected_ocr_keywords:
            return False

        labels = {str(label).lower() for label in (visible_labels or [])}
        lower_ocr = str(ocr_text or "").lower()

        if self.expected_labels:
            return any(expected.lower() in labels for expected in self.expected_labels)

        return any(keyword.lower() in lower_ocr for keyword in self.expected_ocr_keywords)


from context import record_stage_timing


class TaskGraph:
    """Decompose missions into sub-goals and track their completion.

    Usage:
        graph = TaskGraph()
        graph.decompose("Farm the nearest wood node safely", transport=planner.transport)
        current = graph.current_subgoal()
        # ... pass current.description to the planner as the focused goal ...
        graph.advance_if_completed(visible_labels, ocr_text)
    """

    def __init__(self) -> None:
        self.sub_goals: list[SubGoal] = []
        self.current_index = 0
        self.mission = ""
        self.current_goal_cycles_stuck = 0
        self._decomposition_cache: dict[str, list[SubGoal]] = {}

    def current_subgoal(self) -> SubGoal | None:
        """Return the current active sub-goal, or None if all are complete."""
        if self.current_index < len(self.sub_goals):
            return self.sub_goals[self.current_index]
        return None

    def is_complete(self) -> bool:
        """Return True when all sub-goals have been completed."""
        return self.current_index >= len(self.sub_goals)

    def progress_summary(self) -> str:
        """Return a human-readable progress string."""
        if not self.sub_goals:
            return ""
        completed = sum(1 for sg in self.sub_goals if sg.completed)
        total = len(self.sub_goals)
        current = self.current_subgoal()
        summary = f"Progress: {completed}/{total} steps complete."
        if current:
            summary += f" Current: step {current.step} - {current.description}"
        return summary

    def advance_if_completed(
        self,
        visible_labels: list[str] | None = None,
        ocr_text: str = "",
    ) -> bool:
        """Check if the current sub-goal's post-conditions are met and advance.

        Args:
            visible_labels: Currently visible detector labels.
            ocr_text: Currently visible OCR text.

        Returns:
            bool: True if advanced to the next sub-goal.
        """
        current = self.current_subgoal()
        if not current:
            return False
            
        self.current_goal_cycles_stuck += 1
        
        if current.is_completed_by(visible_labels, ocr_text):
            current.completed = True
            LOGGER.info("Sub-goal %s completed: %s", current.step, current.description)
            self._advance_index()
            return True
            
        if self.current_goal_cycles_stuck > 15:
            LOGGER.warning("Sub-goal %s stuck for >15 cycles, fuzzy force-advancing: %s", current.step, current.description)
            current.completed = True
            self._advance_index()
            return True
            
        return False

    def _advance_index(self):
        self.current_index += 1
        self.current_goal_cycles_stuck = 0
        next_goal = self.current_subgoal()
        if next_goal:
            LOGGER.info("Advancing to sub-goal %s: %s", next_goal.step, next_goal.description)
        else:
            LOGGER.info("All sub-goals completed for mission: %s", self.mission)

    def force_advance(self) -> None:
        """Manually skip the current sub-goal (for example, when stuck too long)."""
        current = self.current_subgoal()
        if current:
            LOGGER.warning("Force-advancing past sub-goal %s: %s", current.step, current.description)
            current.completed = True
            self._advance_index()

    def focused_goal_text(self, full_mission: str) -> str:
        """Build a focused goal string for the planner.

        If sub-goals are active, returns the current sub-goal description
        with progress context. Otherwise falls back to the full mission.
        """
        current = self.current_subgoal()
        if not current:
            return full_mission
        return (
            f"[Step {current.step}/{len(self.sub_goals)}] {current.description}\n"
            f"Full mission: {full_mission}\n"
            f"Completion hint: {current.completion_hint}"
        )

    def _single_goal(
        self,
        mission: str,
        *,
        completion_hint: str = "Mission complete.",
    ) -> list[SubGoal]:
        self.sub_goals = [SubGoal(step=1, description=mission, completion_hint=completion_hint)]
        self.current_index = 0
        self.current_goal_cycles_stuck = 0
        return self.sub_goals

    @staticmethod
    def _build_request_payload(mission: str, model: str) -> dict[str, Any]:
        prompt = (
            "You are a Rise of Kingdoms automation planner. Decompose the following "
            "mission into a sequence of 2-8 concrete sub-goals that a screen-based "
            "AI planner can execute one at a time. Each sub-goal should be a single "
            "UI interaction or navigation step.\n\n"
            "For each sub-goal, provide:\n"
            "- step: sequential number\n"
            "- description: what the planner should do\n"
            "- expected_labels: YOLO detector labels that indicate this step's "
            "screen (e.g., 'gatheraction', 'searchaction', 'confirm')\n"
            "- expected_ocr_keywords: text that should appear on screen when this "
            "step is reached\n"
            "- completion_hint: how to know this step is done\n\n"
            f"Mission: {mission}"
        )
        return {
            "model": model,
            "instructions": "Return only the strict JSON object requested by the schema.",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "mission_decomposition",
                    "strict": True,
                    "schema": DECOMPOSITION_SCHEMA,
                }
            },
        }

    @staticmethod
    def _parse_sub_goals(raw_goals: list[dict[str, Any]]) -> list[SubGoal]:
        return [
            SubGoal(
                step=int(goal.get("step", index + 1)),
                description=str(goal.get("description", "")),
                expected_labels=list(goal.get("expected_labels", [])),
                expected_ocr_keywords=list(goal.get("expected_ocr_keywords", [])),
                completion_hint=str(goal.get("completion_hint", "")),
            )
            for index, goal in enumerate(raw_goals)
            if goal.get("description")
        ]

    def decompose(
        self,
        mission: str,
        *,
        transport: PlannerTransport | None = None,
        model: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
        context: Any | None = None,
    ) -> list[SubGoal]:
        """Decompose a mission into sub-goals using one shared planner request.

        Results are cached per mission text to avoid redundant API calls.

        Args:
            mission: Natural-language mission text.
            transport: Shared planner transport. If None, falls back to
                single-goal mode.
            model: OpenAI model name.
            should_cancel: Optional callback that aborts decomposition while
                the runtime is pausing or stopping.
            context: Optional runtime context used for timing telemetry.

        Returns:
            list[SubGoal]: The decomposed sub-goals.
        """
        started_at = time.perf_counter()
        self.mission = mission

        if mission in self._decomposition_cache:
            cached = self._decomposition_cache[mission]
            self.sub_goals = [
                SubGoal(
                    step=sg.step,
                    description=sg.description,
                    expected_labels=list(sg.expected_labels),
                    expected_ocr_keywords=list(sg.expected_ocr_keywords),
                    completion_hint=sg.completion_hint,
                )
                for sg in cached
            ]
            self.current_index = 0
            self.current_goal_cycles_stuck = 0
            LOGGER.info("TaskGraph: reusing cached decomposition (%s sub-goals).", len(self.sub_goals))
            record_stage_timing(
                context,
                "task_graph_decompose",
                started_at,
                detail=f"cache_hit sub_goals={len(self.sub_goals)}",
            )
            return self.sub_goals

        if not transport:
            record_stage_timing(
                context,
                "task_graph_decompose",
                started_at,
                detail="fallback=no_transport",
            )
            return self._single_goal(mission)

        config = ConfigManager()
        model = model or config.get("OPENAI_VISION_MODEL", "gpt-5.4-mini") or "gpt-5.4-mini"
        cancellation = should_cancel or (lambda: False)

        try:
            response = transport.request(self._build_request_payload(mission, model), cancellation)
            if response is None:
                LOGGER.info("TaskGraph decomposition cancelled; using single-goal fallback.")
                record_stage_timing(
                    context,
                    "task_graph_decompose",
                    started_at,
                    detail="cancelled",
                )
                return self._single_goal(mission)
            raw = safe_json_loads(response.output_text)
            self.sub_goals = self._parse_sub_goals(raw.get("sub_goals", []))
        except Exception as exc:
            LOGGER.warning("TaskGraph decomposition failed, using single goal: %s", exc)
            record_stage_timing(
                context,
                "task_graph_decompose",
                started_at,
                detail=f"fallback=error:{type(exc).__name__}",
            )
            return self._single_goal(mission)

        if not self.sub_goals:
            self._single_goal(mission)

        self.current_index = 0
        self.current_goal_cycles_stuck = 0
        self._decomposition_cache[mission] = self.sub_goals
        LOGGER.info(
            "TaskGraph: decomposed mission into %s sub-goals: %s",
            len(self.sub_goals),
            ", ".join(sg.description[:60] for sg in self.sub_goals),
        )
        record_stage_timing(
            context,
            "task_graph_decompose",
            started_at,
            detail=f"sub_goals={len(self.sub_goals)}",
        )
        return self.sub_goals
