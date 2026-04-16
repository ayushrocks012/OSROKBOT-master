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
        return lambda context=None: True

    @staticmethod
    def idle_march_precondition(required=1):
        _ = required
        return lambda context=None: True

    @staticmethod
    def ap_precondition(required=50):
        _ = required
        return lambda context=None: True

    @staticmethod
    def march_and_ap_precondition(required_slots=1, required_ap=50):
        _ = (required_slots, required_ap)
        return lambda context=None: True

    @staticmethod
    def map_and_march_precondition(required_slots=1):
        _ = required_slots
        return lambda context=None: True

    @staticmethod
    def map_march_and_ap_precondition(required_slots=1, required_ap=50):
        _ = (required_slots, required_ap)
        return lambda context=None: True

    def dynamic_planner(self):
        machine = self.create_machine()
        machine.add_state("plan_next", DynamicPlannerAction(), "plan_next", "plan_next")
        machine.set_initial_state("plan_next")
        return machine
