from Actions.spiral_scan_action import SpiralScanAction


class FindGemAction(SpiralScanAction):
    def __init__(self, delay=0.1, post_delay=0):
        super().__init__(
            targets=(
                "Media/gemdepo.png",
                "Media/gemdepo1.png",
                "Media/gemdepo2.png",
            ),
            delay=delay,
            post_delay=post_delay,
            hold_seconds_by_key={
                "left": 0.7,
                "down": 0.5,
                "right": 0.7,
                "up": 0.5,
            },
            stop_on_first=False,
        )
