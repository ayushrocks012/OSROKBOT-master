from Actions.action import Action
from image_finder import ImageFinder
from window_handler import WindowHandler


class FindImageAction(Action):
    def __init__(self, image: str, count: int = 1, delay=0.1, post_delay=0, search_region=None):
        super().__init__(delay=delay, post_delay=post_delay)
        self.image_finder = ImageFinder()
        self.image = image
        self.window_handler = WindowHandler()
        self.window_title = 'Rise of Kingdoms'
        self.count = count  # Number of times the image must be found
        self.search_region = search_region

    def execute(self, context=None):
        window_title = context.window_title if context else self.window_title
        screenshot, win = self.window_handler.screenshot_window(window_title)
        if screenshot is None:
            return False
        scaling_factor, matches, num_matches, _, _, _ = self.image_finder._match_image(
            self.image,
            screenshot,
            search_region=self.search_region,
        )

        # Check if the number of matches is greater or equal to the specified count
        if num_matches >= self.count:
            print(f"Found {self.image} {num_matches} times, satisfying the count condition of {self.count}.", "green")
            return True
        else:
            print(f"Found {self.image} {num_matches} times, not satisfying the count condition of {self.count}.", "red")
            return False
