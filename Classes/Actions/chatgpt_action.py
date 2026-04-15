import json
from Actions.action import Action
import openai
import os
from dotenv import load_dotenv
from Actions.manual_click_action import ManualClickAction
from Actions.manual_move_action import ManualMoveAction
import time
from termcolor import colored
import csv 
from Actions.check_color_action import CheckColorAction

class ChatGPTAction(Action):
    def __init__(self,midterm=False, filepath="string.txt", delay=0, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        load_dotenv()
        openai.api_key = os.getenv('OPENAI_KEY')
        self.message = ""
        self.midterm = midterm
        self.messages = [{"role": "system", "content": "You are a quizz assistant in the game Rise of Kingdoms."}]
        self.functions = [
        {
            "name": "return_option_based_on_prompt",
            "description": "thinking step by step, ignoring the answer options, returns the chosen answer option (A, B, C or D) based on the prompt",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "enum": ["A", "B", "C", "D"],
                        "description": "The chosen answer option to the question in the prompt.",
                    },
                },
                "required": ["answer"],
            },
        }
        ]
    def checkCorrect(self, context=None):
            
            with open('roklyceum.csv', mode='a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                time.sleep(.5)
                if context:
                    if CheckColorAction(40,48).execute(context):
                        writer.writerow([context.Q, context.A])
                    elif CheckColorAction(60,50).execute(context):
                        writer.writerow([context.Q, context.B])
                    elif CheckColorAction(40,58).execute(context):
                        writer.writerow([context.Q, context.C])
                    elif CheckColorAction(60,58).execute(context):
                        writer.writerow([context.Q, context.D])
                else:
                    print("Warning: Context missing in checkCorrect")

    def execute(self, context=None):
        #os.system('cls')
        if context:
            self.message = "Question:" +  (context.Q or "") + "\n" + "A:" + (context.A or "") + "\n" + "B:" + (context.B or "") + "\n" + "C:" + (context.C or "") + "\n" + "D:" + (context.D or "")
        else:
            self.message = "Question:\nA:\nB:\nC:\nD:"
        print("\n\n")
        self.messages.append({"role": "user", "content": (self.message)},)
        chat = openai.chat.completions.create(
            model="gpt-4-1106-preview",
            temperature=.1,
            messages=self.messages,
            functions=self.functions,
            function_call={"name": "return_option_based_on_prompt"}
        )

        print(chat.choices[0].message.function_call.arguments)
        
        function_arguments = json.loads(chat.choices[0].message.function_call.arguments)
        function_response = function_arguments["answer"]

        print("\n\n I think it's " , colored(function_response,"red"))

        self.messages.clear()
        self.messages = [{"role": "system", "content": "You are a quizz assistant in the game Rise of Kingdoms."}]

        # Switch case for reply A, B, C, D, or E
        if not self.midterm:
            if function_response == "A":
                ManualClickAction(40,48).perform(context)
            elif function_response == "B":
                ManualClickAction(60,50).perform(context)
            elif function_response == "C":
                ManualClickAction(40,58).perform(context)
            elif function_response == "D":
                ManualClickAction(60,58).perform(context)
            else:
                print("")
            self.checkCorrect(context)
            
        else:
            if function_response == "A":
                ManualMoveAction(37,55).perform(context)
                print("------A---")
            elif function_response == "B":
                ManualMoveAction(60,55).perform(context)
                print("------B---")
            elif function_response == "C":
                ManualMoveAction(37,63).perform(context)
                print("------C---")
            elif function_response == "D":
                ManualMoveAction(60,63).perform(context)
                print("------D---")
                    

        
        return True
