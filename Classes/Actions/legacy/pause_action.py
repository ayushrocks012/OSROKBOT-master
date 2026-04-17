"""Deprecated bot pause action retained outside the supported runtime."""

from Actions.action import Action


class PauseAction(Action):
    def __init__(self,OS_ROKBOT, delay=0.1, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.OS_ROKBOT = OS_ROKBOT

    def execute(self, context=None):
        #quit the script
        self.OS_ROKBOT.toggle_pause()
        return True
