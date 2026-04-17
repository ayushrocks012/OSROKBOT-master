from __future__ import annotations

from collections.abc import Callable
from typing import Any

from Actions.dynamic_planner_action import DynamicPlannerAction
from logging_config import get_logger
from state_machine import StateMachine

LOGGER = get_logger(__name__)
type PreconditionCallback = Callable[[Any | None], bool]


class ActionSets:
    """Workflow factory.

    OSROKBOT is planner-first. New runtime work should enter through
    `dynamic_planner()` so screenshots, YOLO labels, OCR text, approval policy,
    and visual memory stay in the guarded execution path.
    """

    def __init__(
        self,
        OS_ROKBOT: Any,
        dynamic_planner_factory: Callable[[], DynamicPlannerAction] | None = None,
    ) -> None:
        self.OS_ROKBOT = OS_ROKBOT
        self._dynamic_planner_factory = dynamic_planner_factory or DynamicPlannerAction

    def create_machine(self) -> StateMachine:
        return StateMachine()

    @staticmethod
    def map_view_precondition() -> PreconditionCallback:
        """Precondition: game should be on the world map."""

        def _check(context: Any | None = None) -> bool:
            if not context:
                return True
            try:
                from state_monitor import GameState, GameStateMonitor

                state = GameStateMonitor(context=context).current_state()
                return state in {GameState.MAP, GameState.UNKNOWN}
            except Exception as exc:
                LOGGER.warning("Map-view precondition unavailable: %s", exc)
                return True

        return _check

    @staticmethod
    def idle_march_precondition(required: int = 1) -> PreconditionCallback:
        """Precondition: at least `required` idle march slots."""

        def _check(context: Any | None = None) -> bool:
            if not context:
                return True
            try:
                from state_monitor import GameStateMonitor

                return bool(GameStateMonitor(context=context).has_idle_march_slots(required))
            except Exception as exc:
                LOGGER.warning("Idle-march precondition unavailable: %s", exc)
                return True

        return _check

    @staticmethod
    def ap_precondition(required: int = 50) -> PreconditionCallback:
        """Precondition: at least `required` action points."""

        def _check(context: Any | None = None) -> bool:
            if not context:
                return True
            try:
                from state_monitor import GameStateMonitor

                return bool(GameStateMonitor(context=context).has_action_points(required))
            except Exception as exc:
                LOGGER.warning("Action-point precondition unavailable: %s", exc)
                return True

        return _check

    @staticmethod
    def march_and_ap_precondition(required_slots: int = 1, required_ap: int = 50) -> PreconditionCallback:
        """Precondition: idle march slots AND action points."""
        march_check = ActionSets.idle_march_precondition(required_slots)
        ap_check = ActionSets.ap_precondition(required_ap)

        def _check(context: Any | None = None) -> bool:
            return march_check(context) and ap_check(context)

        return _check

    @staticmethod
    def map_and_march_precondition(required_slots: int = 1) -> PreconditionCallback:
        """Precondition: map view AND idle march slots."""
        map_check = ActionSets.map_view_precondition()
        march_check = ActionSets.idle_march_precondition(required_slots)

        def _check(context: Any | None = None) -> bool:
            return map_check(context) and march_check(context)

        return _check

    @staticmethod
    def map_march_and_ap_precondition(required_slots: int = 1, required_ap: int = 50) -> PreconditionCallback:
        """Precondition: map view AND march slots AND action points."""
        map_check = ActionSets.map_view_precondition()
        march_check = ActionSets.idle_march_precondition(required_slots)
        ap_check = ActionSets.ap_precondition(required_ap)

        def _check(context: Any | None = None) -> bool:
            return map_check(context) and march_check(context) and ap_check(context)

        return _check

    def dynamic_planner(self) -> StateMachine:
        machine = self.create_machine()
        machine.add_state("plan_next", self._dynamic_planner_factory(), "plan_next", "plan_next")
        machine.set_initial_state("plan_next")
        return machine
