from Actions.action import Action, ActionMetadata
from image_finder import ImageFinder
from logging_config import get_logger
from window_handler import WindowHandler

LOGGER = get_logger(__name__)

class FindAndClickImageAction(Action):
    def __init__(
        self,
        image: str,
        offset_x=0,
        offset_y=0,
        delay=0.2,
        post_delay=0,
        max_matches=0,
        search_region=None,
        use_edges=False,
    ):
        super().__init__(delay=delay, post_delay=post_delay)
        self.image_finder = ImageFinder(use_edges=use_edges)
        self.image = image
        self.max_matches = max_matches
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.search_region = search_region
        self.window_handler = WindowHandler()
        self.window_title = 'Rise of Kingdoms'

    def execute(self, context=None):
        from input_controller import InputController
        window_title = context.window_title if context else self.window_title
        screenshot, win = self.window_handler.screenshot_window(window_title)
        if screenshot is None or win is None:
            return False
        
        found, x, y, pick_len = self.image_finder.find_image_coordinates(
            self.image,
            screenshot,
            win,
            self.offset_x,
            self.offset_y,
            self.max_matches,
            search_region=self.search_region,
        )
        
        if found:
            if (pick_len >= self.max_matches and self.max_matches != 0):
                return False
            if (pick_len < self.max_matches and self.max_matches != 0):
                return True
                
            if x is not None and y is not None:
                controller = InputController(context=context)
                return controller.click(x, y, window_rect=win)
            return False
                
        if "captcha" not in str(self.image).lower():
            LOGGER.error(f"No matches for {self.image} found in screenshot.")
        return self.max_matches != 0

    def get_action_metadata(self) -> ActionMetadata:
        detail = "" if "captcha" in str(self.image).lower() else str(self.image)
        return ActionMetadata(
            name=self.__class__.__name__,
            detail=detail,
            delay=self.delay,
            post_delay=self.post_delay,
        )

