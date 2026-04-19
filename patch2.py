import sys
content = open('Classes/input_controller.py', 'r', encoding='utf-8').read()

old1 = """    def __init__(
        self,
        delay_policy: DelayPolicy | None = None,
        context=None,
        coordinate_noise_px: int | None = None,
        move_duration: float | None = None,
        move_steps_per_second=60,
        humanization_profile: HumanizationProfile | None = None,
    ):
        self.delay_policy = delay_policy or DelayPolicy()
        self.context = context
        self.humanization_profile = humanization_profile or HumanizationProfile()"""

new1 = """    def __init__(
        self,
        delay_policy: DelayPolicy | None = None,
        context=None,
        coordinate_noise_px: int | None = None,
        move_duration: float | None = None,
        move_steps_per_second=60,
        humanization_profile: HumanizationProfile | None = None,
        window_handler=None,
    ):
        self.delay_policy = delay_policy or DelayPolicy()
        self.context = context
        self._window_handler = window_handler
        self.humanization_profile = humanization_profile or HumanizationProfile()"""

old2 = """        try:
            from window_handler import WindowHandler

            if WindowHandler().ensure_foreground(window_title, wait_seconds=0.5):
                return True
        except Exception as exc:"""

new2 = """        try:
            handler = self._window_handler
            if handler is None:
                from window_handler import WindowHandler
                handler = WindowHandler()
                self._window_handler = handler

            if handler.ensure_foreground(window_title, wait_seconds=0.5):
                return True
        except Exception as exc:"""

new_content = content.replace(old1, new1).replace(old2, new2)
if content == new_content:
    print('Failed to replace.')
else:
    open('Classes/input_controller.py', 'w', encoding='utf-8').write(new_content)
    print('Success.')
