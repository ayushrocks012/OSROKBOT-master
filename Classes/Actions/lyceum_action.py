import csv
from Actions.action import Action
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel
from Actions.manual_click_action import ManualClickAction
from Actions.manual_move_action import ManualMoveAction

class LyceumAction(Action):
    def __init__(self, midterm=False, delay=0, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.midterm = midterm
        self.score =0
        self.optionScore =0

    def fetch_data_from_csv(self, csv_filename):
        with open(csv_filename, mode='r', encoding='utf-8') as file:
            reader = csv.reader(file)
            next(reader)  # skip the header
            data = [(row[0], row[1]) for row in reader]
        return data

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



    def execute(self, context=None):
        if not context:
            print("Warning: Context is None in LyceumAction")
            return False

        data = self.fetch_data_from_csv("roklyceum.csv")
        questions = [item[0] for item in data]
        answers = [item[1] for item in data]

        # Find the most similar question
        closest_question = self.find_most_similar(context.Q, questions, context)
        answer_index = questions.index(closest_question)
        actual_answer = answers[answer_index]

        options = [context.A, context.B, context.C, context.D]  # Adjust as per the number of options you have

        

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
        
        if not ((self.score >= 0.98 and self.optionScore >=.8) or (self.score > 0.93 and self.optionScore >= 0.85)):
            print("\nI couldn't find the answer in the database, trying with CGPT")
            return False
        else:
            # Switch case for reply A, B, C, D, or E
            if not self.midterm:
                if option_index == 0:
                    ManualClickAction(40,48).perform(context)
                elif option_index == 1:
                    ManualClickAction(60,50).perform(context)
                elif option_index == 2:
                    ManualClickAction(40,58).perform(context)
                elif option_index == 3:
                    ManualClickAction(60,58).perform(context)
            else:
                if option_index == 0:
                    ManualMoveAction(37,55).perform(context)
                elif option_index == 1:
                    ManualMoveAction(60,55).perform(context)
                elif option_index == 2:
                    ManualMoveAction(37,63).perform(context)
                elif option_index == 3:
                    ManualMoveAction(60,63).perform(context)
            return True
