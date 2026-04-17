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


class ConfigProvider(Protocol):
    """Runtime configuration reader contract."""

    def get(self, key: str, default: Any = None) -> Any:
        """Return a configured value or the provided default."""


class PlannerTransport(Protocol):
    """Transport boundary for side-effect-free planner requests."""

    def request(self, request_payload: dict[str, Any], should_cancel: Callable[[], bool]) -> Any | None:
        """Submit a planner request unless cancellation is requested."""

    def close(self) -> None:
        """Release transport resources."""


class EmergencyStopController(Protocol):
    """Emergency stop arming contract used by the bot runner."""

    @classmethod
    def start_once(cls) -> bool:
        """Arm the emergency stop and return whether it is available."""
