from termcolor import colored

from Actions.dynamic_planner_action import DynamicPlannerAction
from state_machine import StateMachine


LEGACY_WORKFLOWS = {
    "scout_explore",
    "farm_barb",
    "farm_barb_all",
    "train_troops",
    "farm_rss",
    "farm_rss_new",
    "farm_gems",
    "loharjr",
    "loharjrt",
    "farm_wood",
    "farm_food",
    "farm_stone",
    "farm_gold",
    "email_captcha",
    "lyceum",
    "lyceumMid",
}


class ActionSets:
    """Workflow factory.

    OSROKBOT is now planner-first. Legacy OpenCV template workflows are kept as
    method names for compatibility, but they route to the DynamicPlanner so no
    deleted root-level ``Media/*.png`` gameplay templates are loaded.
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
        return lambda context=None: True

    @staticmethod
    def ap_precondition(required=50):
        return lambda context=None: True

    @staticmethod
    def march_and_ap_precondition(required_slots=1, required_ap=50):
        return lambda context=None: True

    @staticmethod
    def map_and_march_precondition(required_slots=1):
        return lambda context=None: True

    @staticmethod
    def map_march_and_ap_precondition(required_slots=1, required_ap=50):
        return lambda context=None: True

    def dynamic_planner(self):
        machine = self.create_machine()
        machine.add_state("plan_next", DynamicPlannerAction(), "plan_next", "plan_next")
        machine.set_initial_state("plan_next")
        return machine

    def _legacy_workflow_removed(self, workflow_name):
        print(
            colored(
                f"Workflow '{workflow_name}' used deleted OpenCV templates and now routes to DynamicPlanner.",
                "yellow",
            )
        )
        return self.dynamic_planner()

    def __getattr__(self, name):
        if name in LEGACY_WORKFLOWS:
            return lambda: self._legacy_workflow_removed(name)
        raise AttributeError(name)
