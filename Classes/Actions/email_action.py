from Actions.action import Action
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
from dotenv import load_dotenv
import os

class SendEmailAction(Action):
    def __init__(self,delay=0.1, subject="Captcha detected", body=" ", to_email="", from_email="rokemailsendertest@gmail.com", from_password="prtnezkgfevwihok", smtp_server='smtp.gmail.com', smtp_port=587, post_delay =0):
        super().__init__(delay=delay, post_delay=post_delay)
        load_dotenv()
        self.subject = subject
        self.body = body
        self.to_email = os.getenv('EMAIL')
        self.from_email = from_email
        self.from_password = from_password
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port

    def execute(self, context=None):
        msg = MIMEMultipart()
        msg['From'] = self.from_email
        msg['To'] = os.getenv('EMAIL')
        msg['Subject'] = self.subject + " " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        msg.attach(MIMEText(self.body, 'plain'))

        server = smtplib.SMTP(self.smtp_server, self.smtp_port)
        server.starttls()
        server.login(self.from_email, self.from_password)
        text = msg.as_string()
        server.sendmail(self.from_email, self.to_email, text)
        server.quit()
        return True
