"""Typed boundary contracts shared by the planner-first runtime."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

type TransitionTarget = str | Callable[[], str | None] | None
type Precondition = Callable[[Any | None], bool] | bool | Any | None


class ActionLike(Protocol):
    """Minimum contract required by `StateMachine`."""

    status_text: str

    def perform(self, context: Any | None = None) -> bool:
        """Run one action step and report success."""


class ClientRectLike(Protocol):
    """Window rectangle fields consumed by planner action execution."""

    left: int
    top: int
    width: int
    height: int


class DetectionLike(Protocol):
    """Object detection fields consumed by planner prompting and memory."""

    label: str
    x: float
    y: float
    width: float
    height: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize the detection for planner payloads and datasets."""


class OCRRegionLike(Protocol):
    """OCR region fields consumed by planner prompting."""

    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float


class DetectionProvider(Protocol):
    """Object detector contract for screenshot observations."""

    def detect(self, screenshot: Image.Image | Path | str) -> Sequence[DetectionLike]:
        """Return normalized detections for a screenshot."""


class OCRProvider(Protocol):
    """OCR service contract used by planner context building."""

    def read(self, image_or_roi: Image.Image | Path | str, purpose: str | None = None) -> str:
        """Return plain text for a screenshot or ROI."""

    def read_regions(
        self,
        image_or_roi: Image.Image | Path | str,
        purpose: str | None = None,
    ) -> Sequence[OCRRegionLike]:
        """Return OCR regions for a screenshot or ROI."""


class WindowCaptureProvider(Protocol):
    """Window screenshot contract used by actions and the bot loop."""

    def get_window(self, title: str) -> Any:
        """Return an OS window object for the requested title."""

    def ensure_foreground(self, title: str, wait_seconds: float = 0.5) -> bool:
        """Ensure the target window is foreground before input is sent."""

    def screenshot_window(self, title: str) -> tuple[Image.Image | None, ClientRectLike | None]:
        """Capture the requested game window and return its client rectangle."""


class InputControllerLike(Protocol):
    """Hardware-input contract used by planner execution and recovery."""

    def wait(self, seconds: float | None = None, context: Any | None = None) -> bool:
        """Wait while honoring runtime pause and stop signals."""

    def hotkey(self, *keys: str | int, context: Any | None = None) -> bool:
        """Press a chorded hotkey sequence."""

    def click(
        self,
        x: float,
        y: float,
        window_rect: ClientRectLike | None = None,
        remember_position: bool = True,
        context: Any | None = None,
    ) -> bool:
        """Click one point inside the active game window."""

    def long_press(
        self,
        x: float,
        y: float,
        hold_seconds: float | None = None,
        window_rect: ClientRectLike | None = None,
        remember_position: bool = True,
        context: Any | None = None,
    ) -> bool:
        """Hold one point inside the active game window."""

    def drag(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        window_rect: ClientRectLike | None = None,
        context: Any | None = None,
    ) -> bool:
        """Drag from one point to another inside the active game window."""

    def key_press(
        self,
        key: str | int,
        hold_seconds: float | None = None,
        presses: int = 1,
        context: Any | None = None,
    ) -> bool:
        """Press one keyboard key or character."""


class StateMonitorLike(Protocol):
    """Coarse game-state monitor contract used by preconditions and recovery."""

    def current_state(self) -> Any:
        """Return the current coarse game state."""

    def clear_blockers(self) -> bool:
        """Dismiss obvious modal blockers when present."""

    def save_diagnostic_screenshot(self, label: str = "recovery") -> Any:
        """Persist one diagnostic screenshot when available."""

    def count_idle_march_slots(self, max_age_seconds: float = 30) -> int | None:
        """Return the observed idle march-slot count."""

    def has_idle_march_slots(self, required: int = 1) -> bool:
        """Return whether the required march slots are available."""

    def read_action_points(self, max_age_seconds: float = 30) -> int | None:
        """Return the observed action-point total."""

    def has_action_points(self, required: int = 50) -> bool:
        """Return whether the required action points are available."""

    def restart_client(self) -> bool:
        """Attempt to restart the game client conservatively."""


type InputControllerFactory = Callable[[Any | None], InputControllerLike]
type StateMonitorFactory = Callable[[Any | None], StateMonitorLike]
type WindowHandlerFactory = Callable[[], WindowCaptureProvider]


class ConfigProvider(Protocol):
    """Runtime configuration reader contract."""

    def get(self, key: str, default: Any = None) -> Any:
        """Return a configured value or the provided default."""


class PlannerTransport(Protocol):
    """Transport boundary for side-effect-free planner and task-graph requests."""

    def request(self, request_payload: dict[str, Any], should_cancel: Callable[[], bool]) -> Any | None:
        """Submit a planner-adjacent request unless cancellation is requested."""

    def close(self) -> None:
        """Release transport resources."""


class EmergencyStopController(Protocol):
    """Emergency stop arming contract used by the bot runner."""

    @classmethod
    def start_once(cls) -> bool:
        """Arm the emergency stop and return whether it is available."""
