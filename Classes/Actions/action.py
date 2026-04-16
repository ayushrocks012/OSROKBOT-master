from abc import ABC, abstractmethod
from dataclasses import dataclass

from input_controller import DelayPolicy, InputController

DEFAULT_DELAY_POLICY = DelayPolicy()


@dataclass(frozen=True)
class ActionMetadata:
    name: str
    detail: str = ""
    delay: float = 0
    post_delay: float = 0


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

    def get_action_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            name=self.__class__.__name__,
            delay=self.delay,
            post_delay=self.post_delay,
        )

    @property
    def status_text(self):
        metadata = self.get_action_metadata()
        lines = [
            metadata.name,
            metadata.detail,
            f"{metadata.delay}s delay",
            f"{metadata.post_delay}s post_delay",
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
        if not DEFAULT_DELAY_POLICY.wait(self.delay, context):
            return False
        if not InputController.is_allowed(context):
            return False
        result = self.execute(context) if context else self.execute()
        if not DEFAULT_DELAY_POLICY.wait(self.post_delay, context):
            return False
        return result

    @abstractmethod
    def execute(self, context=None):
        raise NotImplementedError
