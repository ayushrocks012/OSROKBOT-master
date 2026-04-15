import csv
from Actions.action import Action
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel
from Actions.manual_click_action import ManualClickAction
from Actions.manual_move_action import ManualMoveAction
from ai_fallback import AIFallback
from termcolor import colored


class LyceumAction(Action):
    def __init__(self, midterm=False, delay=0, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.midterm = midterm
        self.score = 0
        self.optionScore = 0

    def fetch_data_from_csv(self, csv_filename):
        try:
            with open(csv_filename, mode='r', encoding='utf-8') as file:
                reader = csv.reader(file)
                next(reader)
                return [(row[0], row[1]) for row in reader if len(row) >= 2]
        except FileNotFoundError:
            print(colored(f"Lyceum CSV not found: {csv_filename}", "yellow"))
            return []

    def tokenizer(self, text):
        return list(text)

    def find_most_similar(self, input_text, text_list, context=None):
        tfidf_vectorizer = TfidfVectorizer(tokenizer=self.tokenizer, analyzer='word', token_pattern=None)
        tfidf_matrix = tfidf_vectorizer.fit_transform(text_list)
        input_vec = tfidf_vectorizer.transform([input_text])
        cosine_similarities = linear_kernel(input_vec, tfidf_matrix).flatten()
        
        max_similarity_index = cosine_similarities.argmax()
        
        # Print the highest similarity score
        print(f"Most similar entry '{text_list[max_similarity_index]}' has a similarity score of: {cosine_similarities[max_similarity_index]:.4f} with")
        if context and (context.Q == input_text):
            self.score = cosine_similarities[max_similarity_index]
        else:
            self.optionScore = cosine_similarities[max_similarity_index]
        
        return text_list[max_similarity_index]

    def _apply_option(self, option_index, context):
        if not self.midterm:
            if option_index == 0:
                return ManualClickAction(40, 48).perform(context)
            if option_index == 1:
                return ManualClickAction(60, 50).perform(context)
            if option_index == 2:
                return ManualClickAction(40, 58).perform(context)
            if option_index == 3:
                return ManualClickAction(60, 58).perform(context)
            return False

        if option_index == 0:
            return ManualMoveAction(37, 55).perform(context)
        if option_index == 1:
            return ManualMoveAction(60, 55).perform(context)
        if option_index == 2:
            return ManualMoveAction(37, 63).perform(context)
        if option_index == 3:
            return ManualMoveAction(60, 63).perform(context)
        return False

    def _ai_fallback(self, context, options):
        result = AIFallback().answer_lyceum(context.Q, options)
        if not result:
            return False

        answer = result.get("answer")
        option_map = {"A": 0, "B": 1, "C": 2, "D": 3}
        if answer not in option_map:
            return False

        print(
            colored(
                f"AI Lyceum fallback chose {answer} confidence={result.get('confidence')}",
                "cyan",
            )
        )
        if result.get("reason"):
            print(colored(result["reason"], "cyan"))
        return self._apply_option(option_map[answer], context)

    def execute(self, context=None):
        if not context:
            print(colored("Warning: Context is None in LyceumAction", "yellow"))
            return False

        data = self.fetch_data_from_csv("roklyceum.csv")
        options = [context.A or "", context.B or "", context.C or "", context.D or ""]
        if not data:
            return self._ai_fallback(context, options)

        questions = [item[0] for item in data]
        answers = [item[1] for item in data]

        # Find the most similar question
        closest_question = self.find_most_similar(context.Q, questions, context)
        answer_index = questions.index(closest_question)
        actual_answer = answers[answer_index]

        # Find the most similar option to the answer
        closest_option = self.find_most_similar(actual_answer, options, context)
        option_index = options.index(closest_option)

        # Switch case for reply A, B, C, D, or E
        if option_index == 0:
            print("\nA is the closest match")
        elif option_index == 1:
            print("\nB is the closest match")
        elif option_index == 2:
            print("\nC is the closest match")
        elif option_index == 3:
            print("\nD is the closest match")

        print(f"with : {self.score}")
        
        if not ((self.score >= 0.98 and self.optionScore >= 0.8) or (self.score > 0.93 and self.optionScore >= 0.85)):
            print(colored("\nI couldn't find the answer in the database, trying OpenAI fallback.", "yellow"))
            return self._ai_fallback(context, options)

        return self._apply_option(option_index, context)
