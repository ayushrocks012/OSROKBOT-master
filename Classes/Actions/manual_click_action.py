import time
from Actions.action import Action
from window_handler import WindowHandler
import pyautogui

class ManualClickAction(Action):
    def __init__(self,x=50,y=50, delay=0, remember_position=True, post_delay=0.0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.window_handler = WindowHandler()
        self.window_title = 'Rise of Kingdoms'
        self.x = x
        self.y = y
        self.remember_position = remember_position



    def execute(self, context=None):
        time.sleep(self.delay)
        time.sleep(0.1)
        try:
            prev_active_window = pyautogui.getActiveWindow()
        except Exception:
            prev_active_window = None
        prev_mouse_x, prev_mouse_y = pyautogui.position()
        window_title = context.window_title if context else self.window_title
        window = self.window_handler.get_window(window_title)
        if not window:
            return False
        click_x = int(window.left + window.width * self.x / 100)
        click_y = int(window.top + window.height * self.y / 100)
        pyautogui.click(click_x, click_y)
        if prev_active_window:
            prev_active_window.activate()
        if self.remember_position:
            pyautogui.moveTo(prev_mouse_x, prev_mouse_y)
        return True
