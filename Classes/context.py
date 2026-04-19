import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from logging_config import get_logger
from runtime_contracts import (
    InputControllerFactory,
    InputControllerLike,
    StateMonitorFactory,
    StateMonitorLike,
    WindowCaptureProvider,
    WindowHandlerFactory,
)
from runtime_payloads import (
    NormalizedPoint,
    PlannerPendingPayload,
    RuntimeTimingEntry,
    RuntimeStepScope,
    StateHistoryEntry,
    coerce_decision_payload,
    compute_absolute_point,
    planner_signal_payload,
    runtime_timing_entry,
    serialize_detections,
    serialize_window_rect,
    state_history_entry,
)
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
    state_history: list[StateHistoryEntry] = field(default_factory=list)
    max_state_history: int = 10
    runtime_timing_history: list[RuntimeTimingEntry] = field(default_factory=list)
    max_runtime_timing_history: int = 50
    ui_anchors: dict[str, dict[str, Any]] = field(default_factory=dict)
    primary_ui_anchor: str = "primary"
    primary_anchor_image: str | None = None
    primary_anchor_reference_normalized: tuple[float, float] | None = None
    planner_goal: str = "Safely continue the selected Rise of Kingdoms task."
    planner_autonomy_level: int = 1
    session_logger: SessionLogger | None = None
    window_handler_factory: WindowHandlerFactory | None = None
    input_controller_factory: InputControllerFactory | None = None
    state_monitor_factory: StateMonitorFactory | None = None
    current_observation: ObservationSnapshot | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _thread_local: threading.local = field(default_factory=threading.local, init=False, repr=False)

    @property
    def UI(self) -> Any | None:
        """Backward-compatible alias for older code paths."""
        return self.ui_instance

    def get_signal_emitter(self) -> Any | None:
        if self.signal_emitter:
            return self.signal_emitter
        if self.bot and hasattr(self.bot, "signal_emitter"):
            return self.bot.signal_emitter
        if self.ui_instance and hasattr(self.ui_instance, "OS_ROKBOT"):
            return self.ui_instance.OS_ROKBOT.signal_emitter
        return None

    def emit_state(self, state_text: str) -> None:
        emitter = self.get_signal_emitter()
        if emitter:
            emitter.state_changed.emit(state_text)

    @staticmethod
    def normalize_coordinate(value: float | int | str) -> float:
        value = float(value)
        if value > 1.0:
            return value / 100.0
        return value

    def record_state(
        self,
        state_name: str,
        action_text: str,
        result: bool,
        next_state: str | None = None,
        event: str = "action",
    ) -> None:
        with self._lock:
            self.state_history.append(
                state_history_entry(
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                    event=event,
                    state=state_name,
                    action=action_text,
                    result=bool(result),
                    next_state=next_state,
                )
            )
            if len(self.state_history) > self.max_state_history:
                del self.state_history[: len(self.state_history) - self.max_state_history]

    def record_runtime_timing(self, stage: str, duration_ms: float, detail: str = "") -> None:
        """Persist one bounded runtime timing sample for the current run."""
        entry = runtime_timing_entry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            stage=stage,
            duration_ms=duration_ms,
            detail=detail,
        )
        with self._lock:
            self.runtime_timing_history.append(entry)
            if len(self.runtime_timing_history) > self.max_runtime_timing_history:
                del self.runtime_timing_history[: len(self.runtime_timing_history) - self.max_runtime_timing_history]
            self.extracted["runtime_timings"] = list(self.runtime_timing_history)
        if self.session_logger:
            self.session_logger.record_timing(stage, duration_ms, detail=detail)

    def bind_runtime_machine(self, machine_id: str) -> None:
        """Bind the current thread to one workflow machine identifier."""

        self._thread_local.runtime_machine_id = str(machine_id)

    def clear_runtime_machine(self) -> None:
        """Clear the workflow machine identifier bound to the current thread."""

        if hasattr(self._thread_local, "runtime_machine_id"):
            delattr(self._thread_local, "runtime_machine_id")

    def current_runtime_machine_id(self) -> str | None:
        """Return the workflow machine identifier bound to the current thread."""

        value = getattr(self._thread_local, "runtime_machine_id", None)
        if value in {None, ""}:
            return None
        return str(value)

    def set_active_step_scope(
        self,
        step_id: str,
        state_name: str,
        action_name: str,
        *,
        machine_id: str | None = None,
    ) -> RuntimeStepScope:
        """Store the active logical step scope for the current thread."""

        scope: RuntimeStepScope = {
            "step_id": str(step_id),
            "state_name": str(state_name),
            "action_name": str(action_name),
        }
        resolved_machine_id = machine_id or self.current_runtime_machine_id()
        if resolved_machine_id:
            scope["machine_id"] = str(resolved_machine_id)
        self._thread_local.runtime_step_scope = scope
        return scope

    def active_step_scope(self) -> RuntimeStepScope | None:
        """Return the active logical step scope for the current thread."""

        scope = getattr(self._thread_local, "runtime_step_scope", None)
        if not isinstance(scope, dict):
            return None
        return cast(RuntimeStepScope, dict(scope))

    def update_active_step_scope(
        self,
        *,
        machine_id: str | None = None,
        decision_id: str | None = None,
        approval_id: str | None = None,
        input_id: str | None = None,
    ) -> RuntimeStepScope | None:
        """Merge additional runtime identifiers into the current thread scope."""

        current_scope = self.active_step_scope()
        if current_scope is None:
            return None
        scope: RuntimeStepScope = {}
        if current_scope.get("machine_id") not in {None, ""}:
            scope["machine_id"] = str(current_scope["machine_id"])
        if current_scope.get("step_id") not in {None, ""}:
            scope["step_id"] = str(current_scope["step_id"])
        if current_scope.get("state_name") not in {None, ""}:
            scope["state_name"] = str(current_scope["state_name"])
        if current_scope.get("action_name") not in {None, ""}:
            scope["action_name"] = str(current_scope["action_name"])
        if current_scope.get("decision_id") not in {None, ""}:
            scope["decision_id"] = str(current_scope["decision_id"])
        if current_scope.get("approval_id") not in {None, ""}:
            scope["approval_id"] = str(current_scope["approval_id"])
        if current_scope.get("input_id") not in {None, ""}:
            scope["input_id"] = str(current_scope["input_id"])

        if machine_id in {None, ""}:
            scope.pop("machine_id", None)
        else:
            scope["machine_id"] = str(machine_id)
        if decision_id in {None, ""}:
            scope.pop("decision_id", None)
        else:
            scope["decision_id"] = str(decision_id)
        if approval_id in {None, ""}:
            scope.pop("approval_id", None)
        else:
            scope["approval_id"] = str(approval_id)
        if input_id in {None, ""}:
            scope.pop("input_id", None)
        else:
            scope["input_id"] = str(input_id)
        self._thread_local.runtime_step_scope = scope
        return scope

    def clear_active_step_scope(self) -> None:
        """Remove the active logical step scope from the current thread."""

        if hasattr(self._thread_local, "runtime_step_scope"):
            delattr(self._thread_local, "runtime_step_scope")

    def build_window_handler(self) -> WindowCaptureProvider:
        """Return the per-run window handler for runtime-safe UI access."""

        if self.window_handler_factory is not None:
            return self.window_handler_factory()
        from window_handler import WindowHandler

        return cast(WindowCaptureProvider, WindowHandler())

    def build_input_controller(self) -> InputControllerLike:
        """Return the per-run input controller for guarded hardware input."""

        if self.input_controller_factory is not None:
            return self.input_controller_factory(self)
        from input_controller import InputController

        return cast(InputControllerLike, InputController(context=self))

    def build_state_monitor(self) -> StateMonitorLike:
        """Return the per-run coarse state monitor used by recovery and guards."""

        if self.state_monitor_factory is not None:
            return self.state_monitor_factory(self)
        from state_monitor import GameStateMonitor

        return cast(StateMonitorLike, GameStateMonitor(context=self))

    def save_failure_diagnostic(self, state_name: str = "unknown") -> Path | None:
        from diagnostic_screenshot import save_diagnostic_screenshot

        screenshot, _ = self.build_window_handler().screenshot_window(self.window_title)
        if screenshot is None:
            LOGGER.warning("Diagnostic capture skipped: screenshot unavailable.")
            return None
        screenshot_path = cast(Path | None, save_diagnostic_screenshot(screenshot, label=f"diagnostic_{state_name}"))
        if screenshot_path:
            if self.session_logger and hasattr(self.session_logger, "update_metadata"):
                self.session_logger.update_metadata(diagnostics_path=str(screenshot_path.parent))
            self.export_state_history(screenshot_path.with_suffix(".log"))
        return screenshot_path

    def export_state_history(self, path: Path) -> Path | None:
        try:
            lines = ["OSROKBOT state history", ""]
            with self._lock:
                history = list(self.state_history)
            for entry in history:
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

    def set_current_observation(
        self,
        screenshot: Any,
        window_rect: Any,
        detections: Iterable[Any] | None = None,
    ) -> ObservationSnapshot:
        with self._lock:
            self.current_observation = ObservationSnapshot(
                screenshot=screenshot,
                window_rect=window_rect,
                detections=tuple(detections) if detections is not None else (),
            )
            return self.current_observation

    def clear_current_observation(self) -> None:
        with self._lock:
            self.current_observation = None

    def get_current_observation(self) -> ObservationSnapshot | None:
        with self._lock:
            return self.current_observation

    def clear_current_observation_if(self, observation: ObservationSnapshot | None) -> bool:
        with self._lock:
            if self.current_observation is observation:
                self.current_observation = None
                return True
        return False

    def set_ui_anchor(
        self,
        name: str,
        screen_x: int,
        screen_y: int,
        window_rect: Any,
        reference_normalized: tuple[float, float] | None = None,
    ) -> None:
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
        normalized_x: float | int | str,
        normalized_y: float | int | str,
        window_rect: Any,
        anchor_name: str | None = None,
        reference_normalized: tuple[float, float] | None = None,
    ) -> tuple[int, int]:
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

    def set_pending_planner_decision(
        self,
        decision: object,
        screenshot_path: str | Path | None = None,
        window_rect: Any | None = None,
        detections: Iterable[object] | None = None,
    ) -> PlannerPendingPayload:
        decision_data = coerce_decision_payload(decision)
        rect_data = serialize_window_rect(window_rect)
        detection_data = serialize_detections(detections)
        absolute_point = compute_absolute_point(decision_data, rect_data)
        pending: PlannerPendingPayload = {
            "decision": decision_data,
            "screenshot_path": str(screenshot_path) if screenshot_path else "",
            "window_rect": rect_data,
            "detections": detection_data,
            "absolute_x": absolute_point[0] if absolute_point else None,
            "absolute_y": absolute_point[1] if absolute_point else None,
            "event": threading.Event(),
            "result": None,
            "corrected_point": None,
        }
        with self._lock:
            self.extracted["planner_pending"] = pending
        self.emit_state("Planner approval needed")
        emitter = self.get_signal_emitter()
        if emitter and hasattr(emitter, "planner_decision"):
            payload = planner_signal_payload(pending)
            emitter.planner_decision.emit(payload)
        return pending

    def pending_planner_decision(self) -> PlannerPendingPayload | None:
        with self._lock:
            pending = self.extracted.get("planner_pending")
        if not isinstance(pending, dict):
            return None
        return cast(PlannerPendingPayload, pending)

    def resolve_planner_decision(
        self,
        approved: bool,
        corrected_point: NormalizedPoint | None = None,
    ) -> bool:
        with self._lock:
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

    def clear_pending_planner_decision(self) -> PlannerPendingPayload | None:
        with self._lock:
            pending = self.extracted.pop("planner_pending", None)
        if not isinstance(pending, dict):
            return None
        return cast(PlannerPendingPayload, pending)

    def set_extracted_text(self, description: str, value: str) -> None:
        cleaned_value = value.replace(",", "").replace("\"", "")
        if description in {"Q", "A", "B", "C", "D"}:
            setattr(self, description, cleaned_value)
        elif description:
            self.extracted[description] = cleaned_value
