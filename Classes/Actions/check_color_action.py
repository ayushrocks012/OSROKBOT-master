from colorsys import rgb_to_hsv

from Actions.window_percent_action import WindowPercentAction


class CheckColorAction(WindowPercentAction):
    def __init__(self, x=50, y=50, delay=0, post_delay=0.0):
        super().__init__(x=x, y=y, delay=delay, post_delay=post_delay)

    def execute(self, context=None):
        screenshot, _ = self.screenshot_window(context)
        if screenshot is None:
            return False

        x, y = self.resolve_screenshot_point(screenshot)
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
        print("NOT GREEN ", color)
        return False
