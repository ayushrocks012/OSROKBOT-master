from Actions.action import Action
from window_handler import WindowHandler
from Actions.find_and_click_image_action import FindAndClickImageAction
import pyautogui 
import time

class FindMarauderAction(Action):
    def __init__(self, delay=0.1, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)

    def execute(self, context=None):
        time.sleep(self.delay)
        window_title = context.window_title if context else "Rise of Kingdoms"
        WindowHandler().activate_window(window_title)
        for duration in range(1, 40):  # Loop for 1 to 5 seconds
            if duration % 4 == 1:  # Arrow left
                key = 'left'
            elif duration % 4 == 2:  # Arrow down
                key = 'down'
            elif duration % 4 == 3:  # Arrow right
                key = 'right'
            else:  # Arrow up
                key = 'up'
            
            
            # Simulate pressing the arrow key for 'duration' seconds
            for x in range(1, duration+1):
                pyautogui.keyDown(key)
                time.sleep(0.4)
                pyautogui.keyUp(key)
                if (FindAndClickImageAction('Media/marauder.png').perform(context)):
                    return True
            
            print(duration)
        return False

        
