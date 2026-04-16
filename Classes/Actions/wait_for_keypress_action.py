import keyboard
from Actions.action import Action
from logging_config import get_logger

LOGGER = get_logger(__name__)


class WaitForKeyPressAction(Action):
    def __init__(self, key, msg, delay=0, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.key = key
        self.msg = msg

    def execute(self, context=None):
        LOGGER.info("Press %s to %s", self.key, self.msg)
        keyboard.wait(self.key)
        return True
