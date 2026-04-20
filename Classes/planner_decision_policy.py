"""Shared planner decision safety policy for validation and review gating.

This module centralizes the decision verdict used by the planner, approval
services, and UI overlays. It keeps the "can execute now" and "requires Fix"
rules in one place so pointer-action safety does not drift across modules.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

ALLOWED_ACTION_TYPES = {"click", "wait", "stop", "drag", "long_press", "key", "type"}
POINTER_ACTION_TYPES = {"click", "drag", "long_press"}
MAX_PLANNER_DELAY_SECONDS = 10.0
MIN_PLANNER_CONFIDENCE = 0.70
MIN_L1_REVIEW_CONFIDENCE = 0.10


class DecisionLike(Protocol):
    """Minimum planner decision fields consumed by the safety policy."""

    action_type: str
    target_id: str
    x: float
    y: float
    confidence: float
    delay_seconds: float
    end_x: float
    end_y: float
    key_name: str
    text_content: str
    drag_direction: str
    source: str


@dataclass(frozen=True, slots=True)
class PlannerDecisionVerdict:
    """Safety verdict shared by planning, approval, and UI status layers."""

    action_type: str
    accepted: bool
    execution_ready: bool
    requires_manual_fix: bool
    rejection_reason: str
    confidence: float


def _decision_value(decision: object, key: str, default: Any = None) -> Any:
    if isinstance(decision, Mapping):
        return decision.get(key, default)
    return getattr(decision, key, default)


def _decision_action_type(decision: object) -> str:
    return str(_decision_value(decision, "action_type", "") or "").lower()


def _decision_source(decision: object) -> str:
    return str(_decision_value(decision, "source", "") or "").lower()


def _decision_float(decision: object, key: str, default: float = math.nan) -> float:
    raw = _decision_value(decision, key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _pointer_coordinate_reason(decision: object, *, action_type: str) -> str | None:
    target_id = str(_decision_value(decision, "target_id", "") or "")
    x_value = _decision_float(decision, "x")
    y_value = _decision_float(decision, "y")
    if not target_id:
        return "missing_target_id"
    if not math.isfinite(x_value) or not math.isfinite(y_value):
        return "drag_start_not_finite" if action_type == "drag" else "target_coordinates_not_finite"
    if not 0.0 <= x_value <= 1.0 or not 0.0 <= y_value <= 1.0:
        if action_type == "drag":
            return f"drag_start_out_of_bounds:{x_value:.3f},{y_value:.3f}"
        return f"target_coordinates_out_of_bounds:{x_value:.3f},{y_value:.3f}"
    if action_type != "drag":
        return None

    end_x_value = _decision_float(decision, "end_x")
    end_y_value = _decision_float(decision, "end_y")
    drag_direction = str(_decision_value(decision, "drag_direction", "") or "")
    valid_end = (
        math.isfinite(end_x_value)
        and math.isfinite(end_y_value)
        and 0.0 <= end_x_value <= 1.0
        and 0.0 <= end_y_value <= 1.0
    )
    if not valid_end and not drag_direction:
        return "drag_missing_end_target_or_direction"
    return None


def _decision_rejection_reason(
    decision: object,
    *,
    min_confidence: float,
) -> tuple[str | None, str, float]:
    if decision is None:
        return "not_a_planner_decision", "", math.nan

    action_type = _decision_action_type(decision)
    if action_type not in ALLOWED_ACTION_TYPES:
        return f"unsupported_action:{action_type}", action_type, math.nan

    confidence = _decision_float(decision, "confidence", math.nan)
    if not math.isfinite(confidence):
        return "confidence_not_finite", action_type, confidence

    delay_seconds = _decision_float(decision, "delay_seconds", math.nan)
    if not math.isfinite(delay_seconds) or not 0.0 <= delay_seconds <= MAX_PLANNER_DELAY_SECONDS:
        return f"delay_out_of_bounds:{delay_seconds}", action_type, confidence

    if action_type in POINTER_ACTION_TYPES:
        coordinate_reason = _pointer_coordinate_reason(decision, action_type=action_type)
        if coordinate_reason is not None:
            return coordinate_reason, action_type, confidence
        if confidence < min_confidence:
            return f"confidence_below_threshold:{confidence:.3f}<{min_confidence:.3f}", action_type, confidence
        return None, action_type, confidence

    if action_type == "key":
        key_name = str(_decision_value(decision, "key_name", "") or "")
        if not key_name:
            return "missing_key_name", action_type, confidence
        if confidence < min_confidence:
            return f"confidence_below_threshold:{confidence:.3f}<{min_confidence:.3f}", action_type, confidence
        return None, action_type, confidence

    if action_type == "type":
        text_content = str(_decision_value(decision, "text_content", "") or "")
        if not text_content:
            return "missing_text_content", action_type, confidence
        if confidence < min_confidence:
            return f"confidence_below_threshold:{confidence:.3f}<{min_confidence:.3f}", action_type, confidence
        return None, action_type, confidence

    return None, action_type, confidence


def decision_verdict(
    decision: object,
    *,
    min_confidence: float = MIN_PLANNER_CONFIDENCE,
    l1_review_min_confidence: float = MIN_L1_REVIEW_CONFIDENCE,
) -> PlannerDecisionVerdict:
    """Return one centralized safety verdict for a planner decision."""

    rejection_reason, action_type, confidence = _decision_rejection_reason(
        decision,
        min_confidence=min_confidence,
    )
    source = _decision_source(decision)
    if rejection_reason is None:
        requires_manual_fix = action_type in POINTER_ACTION_TYPES and source == "ai_review"
        execution_ready = not requires_manual_fix
        return PlannerDecisionVerdict(
            action_type=action_type,
            accepted=True,
            execution_ready=execution_ready,
            requires_manual_fix=requires_manual_fix,
            rejection_reason="",
            confidence=confidence,
        )

    reviewable_pointer = (
        action_type in POINTER_ACTION_TYPES
        and rejection_reason.startswith("confidence_below_threshold:")
        and math.isfinite(confidence)
        and confidence >= max(0.0, float(l1_review_min_confidence))
        and _pointer_coordinate_reason(decision, action_type=action_type) is None
    )
    if reviewable_pointer:
        return PlannerDecisionVerdict(
            action_type=action_type,
            accepted=True,
            execution_ready=False,
            requires_manual_fix=True,
            rejection_reason=rejection_reason,
            confidence=confidence,
        )

    return PlannerDecisionVerdict(
        action_type=action_type,
        accepted=False,
        execution_ready=False,
        requires_manual_fix=False,
        rejection_reason=rejection_reason,
        confidence=confidence,
    )


def decision_requires_manual_fix(
    decision: object,
    *,
    min_confidence: float = MIN_PLANNER_CONFIDENCE,
    l1_review_min_confidence: float = MIN_L1_REVIEW_CONFIDENCE,
) -> bool:
    """Return whether a decision must be corrected with `Fix` before execution."""

    return decision_verdict(
        decision,
        min_confidence=min_confidence,
        l1_review_min_confidence=l1_review_min_confidence,
    ).requires_manual_fix
