from Actions.action import Action, ActionMetadata
from input_controller import DelayPolicy


class ManualSleepAction(Action):
    def __init__(self, break_action=False, delay=1, post_delay=0):
        super().__init__(break_action, delay=0, post_delay=post_delay)
        self.break_action = break_action
        self.sleep_seconds = delay

    def get_action_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            name="ManualSleep",
            delay=self.sleep_seconds,
            post_delay=self.post_delay,
        )

    def execute(self, context=None):
        if not DelayPolicy().wait(self.sleep_seconds, context):
            return False
        return not self.break_action
