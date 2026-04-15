from Actions.action import Action
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
from dotenv import load_dotenv
import os

class SendEmailAction(Action):
    def __init__(self, delay=0.1, subject="Captcha detected", body=" ", smtp_server=None, smtp_port=None, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        load_dotenv()
        self.subject = subject
        self.body = body
        self.to_email = os.getenv("EMAIL_TO")
        self.from_email = os.getenv("EMAIL_FROM")
        self.from_password = os.getenv("EMAIL_PASSWORD")
        self.smtp_server = smtp_server or os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(smtp_port or os.getenv("EMAIL_SMTP_PORT", "587"))

    def execute(self, context=None):
        missing = [
            name for name, value in {
                "EMAIL_TO": self.to_email,
                "EMAIL_FROM": self.from_email,
                "EMAIL_PASSWORD": self.from_password,
            }.items()
            if not value
        ]
        if missing:
            print(f"Email notification skipped. Missing environment variables: {', '.join(missing)}")
            return False

        msg = MIMEMultipart()
        msg['From'] = self.from_email
        msg['To'] = self.to_email
        msg['Subject'] = self.subject + " " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        msg.attach(MIMEText(self.body, 'plain'))

        text = msg.as_string()
        with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
            server.starttls()
            server.login(self.from_email, self.from_password)
            server.sendmail(self.from_email, self.to_email, text)
        return True
