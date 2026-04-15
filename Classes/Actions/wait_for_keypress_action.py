import keyboard
from Actions.action import Action

class WaitForKeyPressAction(Action):
    def __init__(self, key, msg, delay=0, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.key = key
        self.msg = msg

    def execute(self, context=None):
        print(f'\nPress {self.key} to {self.msg}\n')
        keyboard.wait(self.key)
        return True
