from dataclasses import dataclass, field
from typing import Any, Optional


DEFAULT_WINDOW_TITLE = "Rise of Kingdoms"


@dataclass
class Context:
    """Runtime state shared by every state machine in one automation run.

    Inputs:
        ui_instance: Optional PyQt UI object that started the run.
        bot: Optional OSROKBOT instance controlling pause/stop state.
        signal_emitter: Optional Qt signal bridge for UI status updates.
        window_title: Target game window title used by window/input actions.

    Outputs:
        Actions mutate `Q`, `A`, `B`, `C`, `D`, and `extracted` with OCR
        results. Actions also call `emit_state()` to update the UI safely.
    """

    ui_instance: Optional[Any] = None
    bot: Optional[Any] = None
    signal_emitter: Optional[Any] = None
    window_title: str = DEFAULT_WINDOW_TITLE
    Q: Optional[str] = None
    A: Optional[str] = None
    B: Optional[str] = None
    C: Optional[str] = None
    D: Optional[str] = None
    extracted: dict[str, Any] = field(default_factory=dict)

    @property
    def UI(self):
        """Backward-compatible alias for older code paths."""
        return self.ui_instance

    def get_signal_emitter(self):
        if self.signal_emitter:
            return self.signal_emitter
        if self.bot and hasattr(self.bot, "signal_emitter"):
            return self.bot.signal_emitter
        if self.ui_instance and hasattr(self.ui_instance, "OS_ROKBOT"):
            return self.ui_instance.OS_ROKBOT.signal_emitter
        return None

    def emit_state(self, state_text):
        emitter = self.get_signal_emitter()
        if emitter:
            emitter.state_changed.emit(state_text)

    def set_extracted_text(self, description, value):
        cleaned_value = value.replace(",", "").replace("\"", "")
        if description in {"Q", "A", "B", "C", "D"}:
            setattr(self, description, cleaned_value)
        elif description:
            self.extracted[description] = cleaned_value
