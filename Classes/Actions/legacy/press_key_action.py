"""Deprecated keypress action retained outside the supported runtime."""

from Actions.action import Action, ActionMetadata
from input_controller import InputController
from window_handler import WindowHandler


class PressKeyAction(Action):
    def __init__(self, key: str, delay=0, post_delay=0, times=1):
        super().__init__(delay=delay, post_delay=post_delay)
        self.key = key
        self.times = times
        self.window_handler = WindowHandler()

    def execute(self, context=None):
        window_title = context.window_title if context else "Rise of Kingdoms"
        self.window_handler.activate_window(window_title)
        return InputController(context=context).key_press(self.key, presses=self.times)

    def get_action_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            name=self.__class__.__name__,
            detail=str(self.key),
            delay=self.delay,
            post_delay=self.post_delay,
        )
