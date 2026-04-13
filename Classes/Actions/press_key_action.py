from Actions.action import Action
from window_handler import WindowHandler
import pyautogui
import time

class PressKeyAction(Action):
    def __init__(self, key: str, delay=0, post_delay=0, times=1):
        self.key = key
        self.delay = delay
        self.post_delay = post_delay
        self.times = times
        self.window_handler = WindowHandler()

    def execute(self):
        time.sleep(self.delay)
        self.window_handler.activate_window()
        #pyautogui.press(self.key, presses=self.times)
        pyautogui.keyDown(self.key)
        time.sleep(1)
        pyautogui.keyUp(self.key)
        #press arrow left
        
        return True  # Always return True since pressing a key will not fail
