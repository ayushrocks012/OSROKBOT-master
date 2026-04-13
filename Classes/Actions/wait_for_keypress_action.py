import keyboard
from Actions.action import Action

class WaitForKeyPressAction(Action):
    def __init__(self, key, msg, delay=0, post_delay=0):
        
        self.key = key
        self.msg = msg
        self.delay = delay
        self.post_delay = post_delay

    def execute(self):
        print(f'\nPress {self.key} to {self.msg}\n')
        keyboard.wait(self.key)
        return True
