from Actions.dynamic_planner_action import DynamicPlannerAction
from state_machine import StateMachine


class ActionSets:
    """Workflow factory.

    OSROKBOT is planner-first. New runtime work should enter through
    `dynamic_planner()` so screenshots, YOLO labels, OCR text, approval policy,
    and visual memory stay in the guarded execution path.
    """

    def __init__(self, OS_ROKBOT):
        self.OS_ROKBOT = OS_ROKBOT

    def create_machine(self):
        return StateMachine()

    @staticmethod
    def map_view_precondition():
        """Precondition: game should be on the world map."""
        def _check(context=None):
            if not context:
                return True
            try:
                from state_monitor import GameState, GameStateMonitor
                state = GameStateMonitor(context=context).current_state()
                return state in {GameState.MAP, GameState.UNKNOWN}
            except Exception:
                return True
        return _check

    @staticmethod
    def idle_march_precondition(required=1):
        """Precondition: at least `required` idle march slots."""
        def _check(context=None):
            if not context:
                return True
            try:
                from state_monitor import GameStateMonitor
                return GameStateMonitor(context=context).has_idle_march_slots(required)
            except Exception:
                return True
        return _check

    @staticmethod
    def ap_precondition(required=50):
        """Precondition: at least `required` action points."""
        def _check(context=None):
            if not context:
                return True
            try:
                from state_monitor import GameStateMonitor
                return GameStateMonitor(context=context).has_action_points(required)
            except Exception:
                return True
        return _check

    @staticmethod
    def march_and_ap_precondition(required_slots=1, required_ap=50):
        """Precondition: idle march slots AND action points."""
        march_check = ActionSets.idle_march_precondition(required_slots)
        ap_check = ActionSets.ap_precondition(required_ap)
        return lambda context=None: march_check(context) and ap_check(context)

    @staticmethod
    def map_and_march_precondition(required_slots=1):
        """Precondition: map view AND idle march slots."""
        map_check = ActionSets.map_view_precondition()
        march_check = ActionSets.idle_march_precondition(required_slots)
        return lambda context=None: map_check(context) and march_check(context)

    @staticmethod
    def map_march_and_ap_precondition(required_slots=1, required_ap=50):
        """Precondition: map view AND march slots AND action points."""
        map_check = ActionSets.map_view_precondition()
        march_check = ActionSets.idle_march_precondition(required_slots)
        ap_check = ActionSets.ap_precondition(required_ap)
        return lambda context=None: map_check(context) and march_check(context) and ap_check(context)

    def dynamic_planner(self):
        machine = self.create_machine()
        machine.add_state("plan_next", DynamicPlannerAction(), "plan_next", "plan_next")
        machine.set_initial_state("plan_next")
        return machine
