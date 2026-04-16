
from Actions.action import Action
from logging_config import get_logger

LOGGER = get_logger(__name__)


class FindMarauderAction(Action):
    """Compatibility shim for the removed marauder template scanner."""

    def __init__(self, delay=0.1, post_delay=0):
        super().__init__(delay=delay, post_delay=post_delay)

    def execute(self, context=None):
        LOGGER.warning("FindMarauderAction skipped: marauder templates were removed; use DynamicPlanner/YOLO.")
        return False
