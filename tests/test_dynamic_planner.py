import math

import pytest
from dynamic_planner import DynamicPlanner, PlannerDecision


def test_safe_json_loads_accepts_wrapped_json():
    raw = 'assistant says: {"action_type": "wait", "label": "none"} done'

    parsed = DynamicPlanner._safe_json_loads(raw)

    assert parsed["action_type"] == "wait"
    assert parsed["label"] == "none"


def test_planner_decision_from_mapping_normalizes_action_type():
    decision = PlannerDecision.from_mapping(
        {
            "thought_process": "Need to gather.",
            "action_type": "CLICK",
            "label": "Gather Button",
            "x": "0.25",
            "y": "0.75",
            "confidence": "0.91",
            "reason": "The gather button is visible.",
        }
    )

    assert decision.action_type == "click"
    assert decision.x == pytest.approx(0.25)
    assert decision.y == pytest.approx(0.75)
    assert decision.confidence == pytest.approx(0.91)


@pytest.mark.parametrize(
    "decision",
    [
        PlannerDecision("t", "click", "bad", -0.1, 0.5, 0.9, "x is outside"),
        PlannerDecision("t", "click", "bad", 0.5, 1.1, 0.9, "y is outside"),
        PlannerDecision("t", "click", "bad", 0.5, 0.5, 0.1, "low confidence"),
        PlannerDecision("t", "drag", "bad", 0.5, 0.5, 0.9, "unsupported action"),
        PlannerDecision("t", "click", "bad", math.nan, 0.5, 0.9, "nan x"),
    ],
)
def test_validate_decision_rejects_unsafe_clicks(decision):
    assert DynamicPlanner.validate_decision(decision) is False


def test_resolve_target_decision_uses_local_detection_center():
    targets = DynamicPlanner.build_targets(
        detections=[
            {
                "label": "Gather Button",
                "x": 0.25,
                "y": 0.75,
                "width": 0.10,
                "height": 0.20,
                "confidence": 0.88,
            }
        ]
    )
    raw = {
        "thought_process": "Need to gather.",
        "action_type": "click",
        "target_id": "det_1",
        "label": "Gather Button",
        "confidence": 0.91,
        "reason": "The gather button is visible.",
    }

    decision = DynamicPlanner.resolve_target_decision(PlannerDecision.from_mapping(raw), targets)

    assert decision is not None
    assert decision.target_id == "det_1"
    assert decision.x == pytest.approx(0.25)
    assert decision.y == pytest.approx(0.75)
    assert DynamicPlanner.validate_decision(decision) is True


def test_resolve_target_decision_rejects_unknown_target_id():
    raw = {
        "thought_process": "Need to gather.",
        "action_type": "click",
        "target_id": "det_99",
        "label": "Gather Button",
        "confidence": 0.91,
        "reason": "The gather button is visible.",
    }

    decision = DynamicPlanner.resolve_target_decision(PlannerDecision.from_mapping(raw), [])

    assert decision is None


def test_validate_decision_rejects_click_without_target_id():
    decision = PlannerDecision("t", "click", "bad", 0.5, 0.5, 0.9, "raw coordinates")

    assert DynamicPlanner.validate_decision(decision) is False


def test_validate_decision_allows_safe_wait_without_coordinates():
    decision = PlannerDecision("t", "wait", "wait", math.nan, math.nan, 0.0, "No safe click.")

    assert DynamicPlanner.validate_decision(decision) is True


def test_planner_decision_delay_seconds_is_clamped():
    high = PlannerDecision.from_mapping({"action_type": "wait", "delay_seconds": 99})
    low = PlannerDecision.from_mapping({"action_type": "wait", "delay_seconds": -3})

    assert high.delay_seconds == pytest.approx(10.0)
    assert low.delay_seconds == pytest.approx(0.0)
