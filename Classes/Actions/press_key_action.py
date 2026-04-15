from Actions.action import Action
from window_handler import WindowHandler
import pyautogui
import time

class PressKeyAction(Action):
    def __init__(self, key: str, delay=0, post_delay=0, times=1):
        super().__init__(delay=delay, post_delay=post_delay)
        self.key = key
        self.times = times
        self.window_handler = WindowHandler()

    def execute(self, context=None):
        time.sleep(self.delay)
        window_title = context.window_title if context else "Rise of Kingdoms"
        self.window_handler.activate_window(window_title)
        for _ in range(self.times):
            pyautogui.keyDown(self.key)
            time.sleep(1)
            pyautogui.keyUp(self.key)
        
        return True  # Always return True since pressing a key will not fail
