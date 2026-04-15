from abc import ABC, abstractmethod
import time


class Action(ABC):
    """Base contract for all bot actions.

    Subclasses set any action-specific data in `__init__()` and implement
    `execute(context=None)`. `execute()` must return `True` for the success
    transition and `False` for the failure transition. Callers should use
    `perform(context)` instead of calling `execute()` directly so UI status
    updates, `delay`, and `post_delay` are applied consistently.
    """

    def __init__(self, skip_check_first_time: bool = False, delay=0, post_delay=0):
        self.skip_check_first_time = skip_check_first_time
        self.first_run = True
        self.performance_multiplier = 1
        self.delay = delay
        self.post_delay = post_delay

    @property
    def status_text(self):
        details = ""
        if hasattr(self, "image") and self.image != "Media/captchachest.png":
            details = str(self.image)
        elif hasattr(self, "key"):
            details = str(self.key)

        lines = [
            self.__class__.__name__,
            details,
            f"{getattr(self, 'delay', 0)}s delay",
            f"{getattr(self, 'post_delay', 0)}s post_delay",
        ]
        status = "\n".join(lines)
        return (
            status.replace("action", "")
            .replace("FindAnd", "")
            .replace(".png", "")
            .replace("Media/", "")
            .replace("Action", "")
        )

    def perform(self, context=None):
        if context:
            context.emit_state(self.status_text)
        time.sleep(getattr(self, "delay", 0))
        result = self.execute(context) if context else self.execute()
        time.sleep(getattr(self, "post_delay", 0))
        return result

    @abstractmethod
    def execute(self, context=None):
        raise NotImplementedError
