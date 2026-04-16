import csv
from pathlib import Path

from Actions.action import Action
from Actions.check_color_action import CheckColorAction
from Actions.manual_click_action import ManualClickAction
from Actions.manual_move_action import ManualMoveAction
from ai_fallback import AIFallback
from input_controller import DelayPolicy
from logging_config import get_logger

LOGGER = get_logger(__name__)


class ChatGPTAction(Action):
    def __init__(self, midterm=False, filepath="string.txt", delay=0, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.midterm = midterm
        self.filepath = filepath

    def checkCorrect(self, context=None):
        with Path("roklyceum.csv").open(mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not DelayPolicy().wait(.5, context):
                return
            if context:
                if CheckColorAction(40, 48).execute(context):
                    writer.writerow([context.Q, context.A])
                elif CheckColorAction(60, 50).execute(context):
                    writer.writerow([context.Q, context.B])
                elif CheckColorAction(40, 58).execute(context):
                    writer.writerow([context.Q, context.C])
                elif CheckColorAction(60, 58).execute(context):
                    writer.writerow([context.Q, context.D])
            else:
                LOGGER.warning("Warning: Context missing in checkCorrect")

    def _apply_answer(self, answer, context=None):
        if not self.midterm:
            if answer == "A":
                ManualClickAction(40, 48).perform(context)
            elif answer == "B":
                ManualClickAction(60, 50).perform(context)
            elif answer == "C":
                ManualClickAction(40, 58).perform(context)
            elif answer == "D":
                ManualClickAction(60, 58).perform(context)
            else:
                return False
            self.checkCorrect(context)
            return True

        if answer == "A":
            ManualMoveAction(37,55).perform(context)
        elif answer == "B":
            ManualMoveAction(60,55).perform(context)
        elif answer == "C":
            ManualMoveAction(37,63).perform(context)
        elif answer == "D":
            ManualMoveAction(60,63).perform(context)
        else:
            return False
        return True

    def execute(self, context=None):
        if not context:
            LOGGER.warning("Warning: Context is None in ChatGPTAction")
            return False

        result = AIFallback().answer_lyceum(
            context.Q,
            [context.A, context.B, context.C, context.D],
        )
        if not result:
            return False

        answer = result.get("answer")
        LOGGER.info(f"AI Lyceum answer: {answer} confidence={result.get('confidence')}")
        if result.get("reason"):
            LOGGER.info(result["reason"])
        return self._apply_answer(answer, context)
