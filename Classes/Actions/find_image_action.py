from Actions.action import Action, ActionMetadata
from image_finder import ImageFinder
from logging_config import get_logger
from window_handler import WindowHandler

LOGGER = get_logger(__name__)


class FindImageAction(Action):
    def __init__(
        self,
        image: str,
        count: int = 1,
        delay=0.1,
        post_delay=0,
        search_region=None,
        use_edges=False,
    ):
        super().__init__(delay=delay, post_delay=post_delay)
        self.image_finder = ImageFinder(use_edges=use_edges)
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
            LOGGER.info(f"Found {self.image} {num_matches} times, satisfying the count condition of {self.count}.")
            return True
        LOGGER.error(f"Found {self.image} {num_matches} times, not satisfying the count condition of {self.count}.")
        return False

    def get_action_metadata(self) -> ActionMetadata:
        detail = "" if "captcha" in str(self.image).lower() else str(self.image)
        return ActionMetadata(
            name=self.__class__.__name__,
            detail=detail,
            delay=self.delay,
            post_delay=self.post_delay,
        )
