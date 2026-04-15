from Actions.spiral_scan_action import SpiralScanAction


class FindMarauderAction(SpiralScanAction):
    def __init__(self, delay=0.1, post_delay=0):
        super().__init__(
            targets=("Media/marauder.png",),
            delay=delay,
            post_delay=post_delay,
            hold_seconds=0.4,
            stop_on_first=True,
        )
