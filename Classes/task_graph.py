"""Multi-step goal decomposition for complex missions.

Decomposes a natural-language mission into ordered sub-goals using one upfront
LLM call, then feeds each sub-goal as focused context to the planner loop.
Sub-goal completion is detected by matching expected post-conditions against
the currently visible labels and OCR text.
"""

from dataclasses import dataclass, field

from config_manager import ConfigManager
from encoding_utils import safe_json_loads
from logging_config import get_logger

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

    def is_completed_by(self, visible_labels=None, ocr_text=""):
        """Check if current observations satisfy this sub-goal's post-conditions.

        A sub-goal is considered complete when at least one expected label is
        visible OR at least one expected OCR keyword is found in the screen text.
        If no post-conditions are set, the sub-goal is never auto-completed.
        """
        if not self.expected_labels and not self.expected_ocr_keywords:
            return False

        labels = {str(label).lower() for label in (visible_labels or [])}
        lower_ocr = str(ocr_text or "").lower()

        # Check label post-conditions.
        for expected in self.expected_labels:
            if expected.lower() in labels:
                return True

        # Check OCR keyword post-conditions.
        return any(keyword.lower() in lower_ocr for keyword in self.expected_ocr_keywords)


class TaskGraph:
    """Decomposes missions into sub-goals and tracks progress.

    Usage:
        graph = TaskGraph()
        graph.decompose("Farm the nearest wood node safely", client)
        current = graph.current_subgoal()
        # ... pass current.description to the planner as the focused goal ...
        graph.advance_if_completed(visible_labels, ocr_text)
    """

    # Cache decompositions to avoid repeated LLM calls for the same mission.
    _decomposition_cache: dict[str, list[SubGoal]] = {}

    def __init__(self):
        self.sub_goals: list[SubGoal] = []
        self.current_index: int = 0
        self.mission: str = ""

    def current_subgoal(self):
        """Return the current active sub-goal, or None if all are complete."""
        if self.current_index < len(self.sub_goals):
            return self.sub_goals[self.current_index]
        return None

    def is_complete(self):
        """Return True when all sub-goals have been completed."""
        return self.current_index >= len(self.sub_goals)

    def progress_summary(self):
        """Return a human-readable progress string."""
        if not self.sub_goals:
            return ""
        completed = sum(1 for sg in self.sub_goals if sg.completed)
        total = len(self.sub_goals)
        current = self.current_subgoal()
        summary = f"Progress: {completed}/{total} steps complete."
        if current:
            summary += f" Current: step {current.step} — {current.description}"
        return summary

    def advance_if_completed(self, visible_labels=None, ocr_text=""):
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
        if current.is_completed_by(visible_labels, ocr_text):
            current.completed = True
            LOGGER.info(
                f"Sub-goal {current.step} completed: {current.description}"
            )
            self.current_index += 1
            next_goal = self.current_subgoal()
            if next_goal:
                LOGGER.info(
                    f"Advancing to sub-goal {next_goal.step}: {next_goal.description}"
                )
            else:
                LOGGER.info("All sub-goals completed for mission: %s", self.mission)
            return True
        return False

    def force_advance(self):
        """Manually skip the current sub-goal (e.g., when stuck too long)."""
        current = self.current_subgoal()
        if current:
            LOGGER.warning(f"Force-advancing past sub-goal {current.step}: {current.description}")
            current.completed = True
            self.current_index += 1

    def focused_goal_text(self, full_mission):
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

    def decompose(self, mission, openai_client=None, model=None):
        """Decompose a mission into sub-goals using an LLM call.

        Results are cached per mission text to avoid redundant API calls.

        Args:
            mission: Natural-language mission text.
            openai_client: OpenAI client instance. If None, falls back to
                single-goal mode (the full mission becomes the only sub-goal).
            model: OpenAI model name.

        Returns:
            list[SubGoal]: The decomposed sub-goals.
        """
        self.mission = mission

        # Check cache first.
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
            LOGGER.info(f"TaskGraph: reusing cached decomposition ({len(self.sub_goals)} sub-goals).")
            return self.sub_goals

        # If no client, fall back to single-goal mode.
        if not openai_client:
            self.sub_goals = [
                SubGoal(step=1, description=mission, completion_hint="Mission complete.")
            ]
            self.current_index = 0
            return self.sub_goals

        config = ConfigManager()
        model = model or config.get("OPENAI_VISION_MODEL", "gpt-5.4-mini") or "gpt-5.4-mini"

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

        try:
            if not hasattr(openai_client, "responses"):
                LOGGER.warning("TaskGraph decomposition unavailable: Responses API not supported.")
                self.sub_goals = [SubGoal(step=1, description=mission)]
                self.current_index = 0
                return self.sub_goals

            response = openai_client.responses.create(
                model=model,
                instructions="Return only the strict JSON object requested by the schema.",
                input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "mission_decomposition",
                        "strict": True,
                        "schema": DECOMPOSITION_SCHEMA,
                    }
                },
            )
            raw = safe_json_loads(response.output_text)
            raw_goals = raw.get("sub_goals", [])
            self.sub_goals = [
                SubGoal(
                    step=int(g.get("step", i + 1)),
                    description=str(g.get("description", "")),
                    expected_labels=list(g.get("expected_labels", [])),
                    expected_ocr_keywords=list(g.get("expected_ocr_keywords", [])),
                    completion_hint=str(g.get("completion_hint", "")),
                )
                for i, g in enumerate(raw_goals)
                if g.get("description")
            ]
        except Exception as exc:
            LOGGER.warning(f"TaskGraph decomposition failed, using single goal: {exc}")
            self.sub_goals = [
                SubGoal(step=1, description=mission, completion_hint="Mission complete.")
            ]

        if not self.sub_goals:
            self.sub_goals = [SubGoal(step=1, description=mission)]

        self.current_index = 0
        # Cache the result.
        self._decomposition_cache[mission] = self.sub_goals
        LOGGER.info(
            f"TaskGraph: decomposed mission into {len(self.sub_goals)} sub-goals: "
            + ", ".join(sg.description[:60] for sg in self.sub_goals)
        )
        return self.sub_goals
