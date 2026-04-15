from Actions.action import Action
import time

class QuitAction(Action):
    def __init__(self,OS_ROKBOT, delay=0.1,post_delay =0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.OS_ROKBOT = OS_ROKBOT

    def execute(self, context=None):
        time.sleep(self.delay)
        #quit the script
        self.OS_ROKBOT.stop()
        return True
