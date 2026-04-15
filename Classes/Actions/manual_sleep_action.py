import time
from Actions.action import Action

class ManualSleepAction(Action):
    def __init__(self, break_action=False, delay=1, post_delay=0):
        super().__init__(break_action, delay=delay, post_delay=post_delay)
        self.break_action = break_action



    def execute(self, context=None):
        time.sleep(self.delay)
        return not self.break_action
