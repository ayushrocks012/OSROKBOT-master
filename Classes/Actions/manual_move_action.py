from Actions.action import Action
from window_handler import WindowHandler
from input_controller import InputController


class ManualMoveAction(Action):
    def __init__(self,x=50,y=50, delay=0, remember_position=False, post_delay=0.0):
        super().__init__(delay=delay, post_delay=post_delay)
        self.window_handler = WindowHandler()
        self.window_title = 'Rise of Kingdoms'
        self.x = x
        self.y = y
        self.remember_position = remember_position



    def execute(self, context=None):
        window_title = context.window_title if context else self.window_title
        window = self.window_handler.get_window(window_title)
        if not window:
            return False
        click_x = int(window.left + window.width * self.x / 100)
        click_y = int(window.top + window.height * self.y / 100)
        return InputController(context=context).move_to(
            click_x,
            click_y,
            window_rect=window,
            remember_position=self.remember_position,
        )
