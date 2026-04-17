"""Typed runtime payload models shared by the planner-first runtime.

This module keeps the shape of cross-module dictionaries explicit without
forcing the rest of the runtime to depend on heavyweight dataclasses for every
ephemeral payload. It is the typed companion to `runtime_contracts.py`, which
defines behavior-level Protocol boundaries.
"""

from __future__ import annotations

from collections.abc import Iterable
from threading import Event
from typing import Any, Literal, TypedDict


class NormalizedPoint(TypedDict):
    """Normalized x/y point in the game window."""

    x: float
    y: float


class SerializedDetection(TypedDict):
    """JSON-safe detector or OCR target payload used by approval overlays."""

    target_id: str
    label: str
    x: float
    y: float
    width: float
    height: float
    confidence: float


class WindowRectPayload(TypedDict):
    """JSON-safe client-rectangle payload."""

    left: int
    top: int
    width: int
    height: int


class StateHistoryEntry(TypedDict):
    """One recorded workflow state transition."""

    timestamp: str
    event: str
    state: str
    action: str
    result: bool
    next_state: str | None


class RuntimeTimingEntry(TypedDict):
    """One recorded runtime timing sample."""

    timestamp: str
    stage: str
    duration_ms: float
    detail: str


class HeartbeatPayload(TypedDict):
    """Heartbeat payload written for the watchdog."""

    timestamp: str
    timestamp_epoch: float
    bot_pid: int
    game_pid: int | None
    window_title: str
    mission: str
    autonomy_level: int
    repo_root: str
    ui_entrypoint: str
    python_executable: str


PlannerApprovalResult = Literal["approved", "rejected"]


class PlannerPendingPayload(TypedDict):
    """Full approval payload stored on `Context.extracted`."""

    decision: dict[str, Any]
    screenshot_path: str
    window_rect: WindowRectPayload
    detections: list[SerializedDetection]
    absolute_x: int | None
    absolute_y: int | None
    event: Event
    result: PlannerApprovalResult | None
    corrected_point: NormalizedPoint | None


class PlannerDecisionSignalPayload(TypedDict):
    """Subset of planner approval data emitted to the UI."""

    decision: dict[str, Any]
    screenshot_path: str
    window_rect: WindowRectPayload
    detections: list[SerializedDetection]
    absolute_x: int | None
    absolute_y: int | None


class PendingRecoveryPayload(TypedDict):
    """Recovery payload staged between guarded click and verification."""

    signature: str
    screenshot_hash: str
    state_name: str
    action_class: str
    action_image: str
    visible_labels: list[str]
    label: str
    normalized_point: NormalizedPoint
    confidence: float
    source: str


class ResourceContext(TypedDict, total=False):
    """Planner-facing resource context from coarse OCR checks."""

    idle_march_slots: int
    action_points: int


def coerce_decision_payload(decision: object) -> dict[str, Any]:
    """Convert planner decision-like objects into a plain dictionary."""
    raw = decision.to_dict() if hasattr(decision, "to_dict") else decision
    return dict(raw) if isinstance(raw, dict) else {}


def serialize_window_rect(window_rect: object | None) -> WindowRectPayload:
    """Convert a client-rect-like object into a JSON-safe payload."""
    if window_rect is None:
        return {"left": 0, "top": 0, "width": 0, "height": 0}
    return {
        "left": int(getattr(window_rect, "left", 0)),
        "top": int(getattr(window_rect, "top", 0)),
        "width": int(getattr(window_rect, "width", 0)),
        "height": int(getattr(window_rect, "height", 0)),
    }


def serialize_detections(detections: Iterable[object] | None) -> list[SerializedDetection]:
    """Convert detector or OCR targets into approval-overlay payloads."""
    serialized: list[SerializedDetection] = []
    for index, detection in enumerate(detections if detections is not None else (), start=1):
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


def compute_absolute_point(
    decision_payload: dict[str, Any],
    window_rect: WindowRectPayload,
) -> tuple[int, int] | None:
    """Resolve a normalized planner decision point into screen coordinates."""
    if not window_rect["width"] or not window_rect["height"]:
        return None
    try:
        return (
            int(round(window_rect["left"] + window_rect["width"] * float(decision_payload.get("x", 0.0)))),
            int(round(window_rect["top"] + window_rect["height"] * float(decision_payload.get("y", 0.0)))),
        )
    except (TypeError, ValueError):
        return None


def planner_signal_payload(pending: PlannerPendingPayload) -> PlannerDecisionSignalPayload:
    """Drop synchronization fields before emitting planner approval to the UI."""
    return {
        "decision": pending["decision"],
        "screenshot_path": pending["screenshot_path"],
        "window_rect": pending["window_rect"],
        "detections": pending["detections"],
        "absolute_x": pending["absolute_x"],
        "absolute_y": pending["absolute_y"],
    }


def state_history_entry(
    *,
    timestamp: str,
    event: str,
    state: str,
    action: str,
    result: bool,
    next_state: str | None,
) -> StateHistoryEntry:
    """Build one typed state-history entry."""
    return {
        "timestamp": timestamp,
        "event": event,
        "state": state,
        "action": action,
        "result": result,
        "next_state": next_state,
    }


def runtime_timing_entry(
    *,
    timestamp: str,
    stage: str,
    duration_ms: float,
    detail: str = "",
) -> RuntimeTimingEntry:
    """Build one typed runtime timing entry."""
    return {
        "timestamp": timestamp,
        "stage": stage,
        "duration_ms": round(max(0.0, float(duration_ms)), 2),
        "detail": detail,
    }
