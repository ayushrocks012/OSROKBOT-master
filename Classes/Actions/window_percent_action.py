from Actions.action import Action
from window_handler import WindowHandler


class WindowPercentAction(Action):
    """Base action for targets expressed in normalized client coordinates.

    New callers should pass values from `0.0` to `1.0`. Legacy workflow values
    from `0` to `100` are still accepted and converted to normalized values.
    """

    def __init__(self, x=50, y=50, delay=0, post_delay=0.0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.window_handler = WindowHandler()
        self.window_title = "Rise of Kingdoms"
        self.x = x
        self.y = y

    @staticmethod
    def normalize_coordinate(value):
        value = float(value)
        if value > 1.0:
            return value / 100.0
        return value

    def get_window(self, context=None):
        window_title = context.window_title if context else self.window_title
        return self.window_handler.get_window(window_title)

    def screenshot_window(self, context=None):
        window_title = context.window_title if context else self.window_title
        return self.window_handler.screenshot_window(window_title)

    def resolve_window_point(self, window):
        normalized_x = self.normalize_coordinate(self.x)
        normalized_y = self.normalize_coordinate(self.y)
        return (
            int(window.left + window.width * normalized_x),
            int(window.top + window.height * normalized_y),
        )

    def resolve_screenshot_point(self, screenshot):
        normalized_x = self.normalize_coordinate(self.x)
        normalized_y = self.normalize_coordinate(self.y)
        return (
            int(screenshot.width * normalized_x),
            int(screenshot.height * normalized_y),
        )


class WindowPointInputAction(WindowPercentAction):
    """Base action for input operations targeting normalized client coordinates."""

    def __init__(self, x=50, y=50, delay=0, remember_position=False, post_delay=0.0):
        super().__init__(x=x, y=y, delay=delay, post_delay=post_delay)
        self.remember_position = remember_position

    def execute(self, context=None):
        window = self.get_window(context)
        if not window:
            return False
        target_x, target_y = self.resolve_window_point(window)
        return self.execute_input(context, window, target_x, target_y)

    def execute_input(self, context, window, target_x, target_y):
        raise NotImplementedError
