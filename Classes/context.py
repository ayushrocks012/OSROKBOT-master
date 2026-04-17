import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from logging_config import get_logger
from session_logger import SessionLogger

LOGGER = get_logger(__name__)


DEFAULT_WINDOW_TITLE = "Rise of Kingdoms"


@dataclass(frozen=True)
class ObservationSnapshot:
    """One per-step observation reused across safety and planning checks."""

    screenshot: Any
    window_rect: Any
    detections: tuple[Any, ...] = ()


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

    ui_instance: Any | None = None
    bot: Any | None = None
    signal_emitter: Any | None = None
    window_title: str = DEFAULT_WINDOW_TITLE
    Q: str | None = None
    A: str | None = None
    B: str | None = None
    C: str | None = None
    D: str | None = None
    extracted: dict[str, Any] = field(default_factory=dict)
    idle_march_slots: int | None = None
    idle_march_slots_checked_at: float | None = None
    action_points: int | None = None
    action_points_checked_at: float | None = None
    state_history: list[dict[str, Any]] = field(default_factory=list)
    max_state_history: int = 10
    ui_anchors: dict[str, dict[str, Any]] = field(default_factory=dict)
    primary_ui_anchor: str = "primary"
    primary_anchor_image: str | None = None
    primary_anchor_reference_normalized: tuple[float, float] | None = None
    planner_goal: str = "Safely continue the selected Rise of Kingdoms task."
    planner_autonomy_level: int = 1
    session_logger: SessionLogger | None = None
    current_observation: ObservationSnapshot | None = None

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

    @staticmethod
    def normalize_coordinate(value):
        value = float(value)
        if value > 1.0:
            return value / 100.0
        return value

    def record_state(self, state_name, action_text, result, next_state=None, event="action"):
        self.state_history.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "event": event,
                "state": state_name,
                "action": action_text,
                "result": bool(result),
                "next_state": next_state,
            }
        )
        if len(self.state_history) > self.max_state_history:
            del self.state_history[: len(self.state_history) - self.max_state_history]

    def save_failure_diagnostic(self, state_name="unknown"):
        from diagnostic_screenshot import save_diagnostic_screenshot
        from window_handler import WindowHandler

        screenshot, _ = WindowHandler().screenshot_window(self.window_title)
        if screenshot is None:
            LOGGER.warning("Diagnostic capture skipped: screenshot unavailable.")
            return None
        screenshot_path = save_diagnostic_screenshot(screenshot, label=f"diagnostic_{state_name}")
        if screenshot_path:
            self.export_state_history(screenshot_path.with_suffix(".log"))
        return screenshot_path

    def export_state_history(self, path):
        try:
            lines = ["OSROKBOT state history", ""]
            for entry in self.state_history:
                lines.append(
                    "{timestamp} [{event}] state={state} result={result} next={next_state} action={action}".format(
                        timestamp=entry.get("timestamp", ""),
                        event=entry.get("event", ""),
                        state=entry.get("state", ""),
                        result=entry.get("result", ""),
                        next_state=entry.get("next_state", ""),
                        action=str(entry.get("action", "")).replace("\n", " | "),
                    )
                )
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception as exc:
            LOGGER.error(f"Unable to export state history: {exc}")
            return None

        LOGGER.warning(f"State history saved: {path}")
        return path

    def set_current_observation(self, screenshot, window_rect, detections=None):
        self.current_observation = ObservationSnapshot(
            screenshot=screenshot,
            window_rect=window_rect,
            detections=tuple(detections or ()),
        )
        return self.current_observation

    def clear_current_observation(self):
        self.current_observation = None

    @staticmethod
    def _serialize_detections(detections):
        serialized = []
        for index, detection in enumerate(detections or (), start=1):
            raw = detection.to_dict() if hasattr(detection, "to_dict") else detection
            if not isinstance(raw, dict):
                continue
            serialized.append(
                {
                    "target_id": str(raw.get("target_id") or f"det_{index}"),
                    "label": str(raw.get("label", "")),
                    "x": float(raw.get("x", 0.0)),
                    "y": float(raw.get("y", 0.0)),
                    "width": float(raw.get("width", 0.0)),
                    "height": float(raw.get("height", 0.0)),
                    "confidence": float(raw.get("confidence", 0.0)),
                }
            )
        return serialized

    def set_ui_anchor(self, name, screen_x, screen_y, window_rect, reference_normalized=None):
        normalized_x = (int(screen_x) - int(window_rect.left)) / max(1, int(window_rect.width))
        normalized_y = (int(screen_y) - int(window_rect.top)) / max(1, int(window_rect.height))
        reference = reference_normalized or (normalized_x, normalized_y)
        self.ui_anchors[name] = {
            "screen": (int(screen_x), int(screen_y)),
            "client": (int(screen_x) - int(window_rect.left), int(screen_y) - int(window_rect.top)),
            "normalized": (normalized_x, normalized_y),
            "reference_normalized": reference,
            "window_size": (int(window_rect.width), int(window_rect.height)),
            "captured_at": datetime.now().isoformat(timespec="seconds"),
        }
        LOGGER.info(f"UI anchor '{name}' stored at normalized=({normalized_x:.3f},{normalized_y:.3f})")

    def resolve_anchor_relative_point(
        self,
        normalized_x,
        normalized_y,
        window_rect,
        anchor_name=None,
        reference_normalized=None,
    ):
        anchor_key = anchor_name or self.primary_ui_anchor
        anchor = self.ui_anchors.get(anchor_key)
        normalized_x = self.normalize_coordinate(normalized_x)
        normalized_y = self.normalize_coordinate(normalized_y)

        if not anchor:
            return (
                int(window_rect.left + window_rect.width * normalized_x),
                int(window_rect.top + window_rect.height * normalized_y),
            )

        reference_x, reference_y = reference_normalized or anchor["reference_normalized"]
        anchor_screen_x, anchor_screen_y = anchor["screen"]
        offset_x = (normalized_x - reference_x) * int(window_rect.width)
        offset_y = (normalized_y - reference_y) * int(window_rect.height)
        return int(round(anchor_screen_x + offset_x)), int(round(anchor_screen_y + offset_y))

    def set_pending_planner_decision(self, decision, screenshot_path=None, window_rect=None, detections=None):
        decision_data = decision.to_dict() if hasattr(decision, "to_dict") else decision
        rect_data = {
            "left": getattr(window_rect, "left", 0),
            "top": getattr(window_rect, "top", 0),
            "width": getattr(window_rect, "width", 0),
            "height": getattr(window_rect, "height", 0),
        } if window_rect else {}
        detection_data = self._serialize_detections(detections)
        absolute_x = None
        absolute_y = None
        if rect_data and decision_data:
            try:
                absolute_x = int(round(rect_data["left"] + rect_data["width"] * float(decision_data.get("x", 0.0))))
                absolute_y = int(round(rect_data["top"] + rect_data["height"] * float(decision_data.get("y", 0.0))))
            except Exception:
                absolute_x = None
                absolute_y = None

        self.extracted["planner_pending"] = {
            "decision": decision_data,
            "screenshot_path": str(screenshot_path) if screenshot_path else "",
            "window_rect": rect_data,
            "detections": detection_data,
            "absolute_x": absolute_x,
            "absolute_y": absolute_y,
            "event": threading.Event(),
            "result": None,
            "corrected_point": None,
        }
        self.emit_state("Planner approval needed")
        emitter = self.get_signal_emitter()
        if emitter and hasattr(emitter, "planner_decision"):
            payload = {
                "decision": decision_data,
                "screenshot_path": str(screenshot_path) if screenshot_path else "",
                "window_rect": rect_data,
                "detections": detection_data,
                "absolute_x": absolute_x,
                "absolute_y": absolute_y,
            }
            emitter.planner_decision.emit(payload)
        return self.extracted["planner_pending"]

    def resolve_planner_decision(self, approved, corrected_point=None):
        pending = self.extracted.get("planner_pending")
        if not pending:
            return False
        pending["result"] = "approved" if approved else "rejected"
        if corrected_point:
            pending["corrected_point"] = corrected_point
        event = pending.get("event")
        if event:
            event.set()
        return True

    def clear_pending_planner_decision(self):
        return self.extracted.pop("planner_pending", None)

    def set_extracted_text(self, description, value):
        cleaned_value = value.replace(",", "").replace("\"", "")
        if description in {"Q", "A", "B", "C", "D"}:
            setattr(self, description, cleaned_value)
        elif description:
            self.extracted[description] = cleaned_value
