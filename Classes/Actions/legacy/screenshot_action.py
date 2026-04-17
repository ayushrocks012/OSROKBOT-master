"""Deprecated screenshot action retained outside the supported runtime."""

from pathlib import Path

from Actions.action import Action
from Actions.legacy.window_percent_action import WindowPercentAction
from window_handler import WindowHandler


class ScreenshotAction(Action):
    def __init__(self, x_begin, x_end, y_begin, y_end, output_path="test.png", delay=0, post_delay =0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.x_begin = x_begin
        self.x_end = x_end
        self.y_begin = y_begin
        self.y_end = y_end
        self.output_path = output_path
        self.window_title = "Rise of Kingdoms"
        self.window_handler = WindowHandler()

    @staticmethod
    def _normalize_coordinate(value):
        return WindowPercentAction.normalize_coordinate(value)

    def _normalized_box(self):
        x_begin = self._normalize_coordinate(self.x_begin)
        x_end = self._normalize_coordinate(self.x_end)
        y_begin = self._normalize_coordinate(self.y_begin)
        y_end = self._normalize_coordinate(self.y_end)
        return x_begin, x_end, y_begin, y_end

    def execute(self, context=None):
        #print exact time of execution
        window_title = context.window_title if context else self.window_title
        screenshot, win = self.window_handler.screenshot_window(window_title)
        if screenshot is None:
            return False
        
        # Crop screenshot
        width, height = screenshot.size
        x_begin, x_end, y_begin, y_end = self._normalized_box()
        left = width * x_begin
        upper = height * y_begin
        right = width * x_end
        lower = height * y_end
        
        cropped_screenshot = screenshot.crop((left, upper, right, lower))

        output_path = Path(self.output_path)
        output_path.unlink(missing_ok=True)
        cropped_screenshot.save(output_path)
        return True
