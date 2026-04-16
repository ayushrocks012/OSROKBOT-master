from termcolor import colored

from Actions.action import Action


class FindMarauderAction(Action):
    """Compatibility shim for the removed marauder template scanner."""

    def __init__(self, delay=0.1, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)

    def execute(self, context=None):
        print(colored("FindMarauderAction skipped: marauder templates were removed; use DynamicPlanner/YOLO.", "yellow"))
        return False
