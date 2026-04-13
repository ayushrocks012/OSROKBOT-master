from Actions.action import Action
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from Actions.action import Action
import time

class PauseAction(Action):
    def __init__(self,OS_ROKBOT, delay=0.1, post_delay=0):
        self.delay = delay
        self.post_delay =post_delay
        self.OS_ROKBOT = OS_ROKBOT

    def execute(self):
        time.sleep(self.delay)
        #quit the script
        self.OS_ROKBOT.toggle_pause()
        return True
