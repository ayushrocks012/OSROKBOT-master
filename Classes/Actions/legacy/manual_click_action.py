"""Deprecated manual click action retained outside the supported runtime."""

from Actions.legacy.window_percent_action import WindowPointInputAction
from input_controller import InputController


class ManualClickAction(WindowPointInputAction):
    def __init__(self, x=50, y=50, delay=0, remember_position=True, post_delay=0.0):
        super().__init__(x=x, y=y, delay=delay, remember_position=remember_position, post_delay=post_delay)

    def execute_input(self, context, window, target_x, target_y):
        return InputController(context=context).click(
            target_x,
            target_y,
            window_rect=window,
            remember_position=self.remember_position,
        )
