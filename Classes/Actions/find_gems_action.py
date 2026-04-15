from Actions.action import Action
from window_handler import WindowHandler
from Actions.find_and_click_image_action import FindAndClickImageAction
import pyautogui 
import time

class FindGemAction(Action):
    def __init__(self, delay=0.1, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)

    def execute(self, context=None):
        sleeptime=0.4
        found=0
        time.sleep(self.delay)
        window_title = context.window_title if context else "Rise of Kingdoms"
        WindowHandler().activate_window(window_title)
        for duration in range(1, 40):  # Loop for 1 to 5 seconds
            if duration % 4 == 1:  # Arrow left
                key = 'left'
                sleeptime=.7
            elif duration % 4 == 2:  # Arrow down
                key = 'down'
                sleeptime=0.5
            elif duration % 4 == 3:  # Arrow right
                key = 'right'
                sleeptime=.7
            else:  # Arrow up
                key = 'up'
                sleeptime=0.5
            
            
            # Simulate pressing the arrow key for 'duration' seconds
            for x in range(1, duration+1):
                pyautogui.keyDown(key)
                time.sleep(sleeptime)
                pyautogui.keyUp(key)
                if (FindAndClickImageAction('Media/gemdepo.png').perform(context) or FindAndClickImageAction('Media/gemdepo1.png').perform(context) or FindAndClickImageAction('Media/gemdepo2.png').perform(context)):
                    found+=1
                    print("found ", found)
            
            print(duration)
        return found > 0
            
