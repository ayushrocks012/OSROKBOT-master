from Actions.action import Action
from window_handler import WindowHandler


class WindowPercentAction(Action):
    """Base action for targets expressed as percentages of the game window."""

    def __init__(self, x=50, y=50, delay=0, post_delay=0.0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.window_handler = WindowHandler()
        self.window_title = "Rise of Kingdoms"
        self.x = x
        self.y = y

    def get_window(self, context=None):
        window_title = context.window_title if context else self.window_title
        return self.window_handler.get_window(window_title)

    def screenshot_window(self, context=None):
        window_title = context.window_title if context else self.window_title
        return self.window_handler.screenshot_window(window_title)

    def resolve_window_point(self, window):
        return (
            int(window.left + window.width * self.x / 100),
            int(window.top + window.height * self.y / 100),
        )

    def resolve_screenshot_point(self, screenshot):
        return (
            int(screenshot.width * self.x / 100),
            int(screenshot.height * self.y / 100),
        )


class WindowPointInputAction(WindowPercentAction):
    """Base action for input operations targeting a window percentage."""

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
