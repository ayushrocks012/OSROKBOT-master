from Actions.action import Action
from Actions.find_and_click_image_action import FindAndClickImageAction
from input_controller import InputController
from window_handler import WindowHandler


class SpiralScanAction(Action):
    """Move the map in an expanding spiral while checking target templates."""

    DIRECTION_SEQUENCE = ("left", "down", "right", "up")

    def __init__(
        self,
        targets,
        delay=0.1,
        post_delay=0,
        max_duration=39,
        hold_seconds=0.4,
        hold_seconds_by_key=None,
        stop_on_first=True,
    ):
        super().__init__(delay=delay, post_delay=post_delay)
        self.targets = tuple(targets)
        self.max_duration = int(max_duration)
        self.hold_seconds = hold_seconds
        self.hold_seconds_by_key = hold_seconds_by_key or {}
        self.stop_on_first = stop_on_first

    def _movement_for_duration(self, duration):
        key = self.DIRECTION_SEQUENCE[(duration - 1) % len(self.DIRECTION_SEQUENCE)]
        return key, self.hold_seconds_by_key.get(key, self.hold_seconds)

    def _find_target(self, context=None):
        for target in self.targets:
            if FindAndClickImageAction(target).perform(context):
                return True
        return False

    def execute(self, context=None):
        found = 0
        controller = InputController(context=context)
        window_title = context.window_title if context else "Rise of Kingdoms"
        WindowHandler().activate_window(window_title)

        for duration in range(1, self.max_duration + 1):
            key, hold_seconds = self._movement_for_duration(duration)

            for _ in range(1, duration + 1):
                if not controller.key_press(key, hold_seconds=hold_seconds):
                    return found > 0
                if self._find_target(context):
                    found += 1
                    print("found ", found)
                    if self.stop_on_first:
                        return True

            print(duration)
        return found > 0
