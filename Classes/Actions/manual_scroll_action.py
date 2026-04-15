
from Actions.action import Action
from pynput.mouse import  Controller

class ManualScrollAction(Action):
    def __init__(self, y_scroll=0, x_pos= 0, y_pos=0, delay=0, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.y_scroll = y_scroll
        self.mouse = Controller()

    def execute(self, context=None):
        for i in range(self.y_scroll):
            self.mouse.scroll(0, -1)
        return True
