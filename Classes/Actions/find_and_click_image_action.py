from Actions.action import Action
from image_finder import ImageFinder
from window_handler import WindowHandler
import time
class FindAndClickImageAction(Action):
    def __init__(self, image: str,offset_x= 0, offset_y= 0, delay=0.2, post_delay=0, max_matches=0 ):
        
        self.delay = delay
        self.image_finder = ImageFinder()
        self.image = image
        self.max_matches = max_matches
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.window_handler = WindowHandler()
        self.window_title = 'Rise of Kingdoms'
        self.post_delay = post_delay

    def execute(self):
        from input_controller import InputController
        screenshot, win = self.window_handler.screenshot_window(self.window_title)
        
        found, x, y, pick_len = self.image_finder.find_image_coordinates(self.image, screenshot, win, self.offset_x, self.offset_y, self.max_matches)
        
        if found:
            if (pick_len >= self.max_matches and self.max_matches != 0):
                return False
            if (pick_len < self.max_matches and self.max_matches != 0):
                return True
                
            if x is not None and y is not None:
                controller = InputController()
                return controller.click(x, y)
                
        else:
            if self.image != "Media/captchachest.png":
                print(f"No matches for {self.image} found in screenshot.")
            if self.max_matches != 0:
                return True
            return False

