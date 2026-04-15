import time
from Actions.action import Action
from window_handler import WindowHandler
from colorsys import rgb_to_hsv

class CheckColorAction(Action):
    def __init__(self,x=50,y=50, delay=0, post_delay=0.0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.window_handler = WindowHandler()
        self.window_title = 'Rise of Kingdoms'
        self.x = x
        self.y = y



    def execute(self, context=None):
        window_title = context.window_title if context else self.window_title
        screenshot, win = self.window_handler.screenshot_window(window_title)
        if screenshot is None:
            return False

        x = int(screenshot.width * self.x / 100)
        y = int(screenshot.height * self.y / 100)
        screenshot.save("idk.png")
        print("x ", x, " y ", y)

        # Extract the color at the center of the screenshot
        color = screenshot.getpixel((x, y))
        

        # Convert RGB to HSV for easier color comparison
        r, g, b = color
        target_h, _, _ = rgb_to_hsv(0, 255, 0)  # HSV value of pure green
        color_h, _, _ = rgb_to_hsv(r, g, b)

        # Set a threshold for color difference (adjust as needed)
        threshold = 10.0 / 360.0  # 10 degrees in the HSV color space

        # Check if the color is close to green
        if abs(color_h - target_h) < threshold:
            print("Green: ", color)
            return True
        else:
            print("NOT GREEN ", color)
            return False
