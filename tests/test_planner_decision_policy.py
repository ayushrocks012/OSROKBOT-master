from dynamic_planner import PlannerDecision
from planner_decision_policy import decision_requires_manual_fix, decision_verdict


def test_decision_verdict_accepts_safe_click():
    decision = PlannerDecision(
        "t",
        "click",
        "Gather Button",
        0.25,
        0.50,
        0.91,
        "Visible target.",
        target_id="det_1",
    )

    verdict = decision_verdict(decision)

    assert verdict.accepted is True
    assert verdict.execution_ready is True
    assert verdict.requires_manual_fix is False
    assert verdict.rejection_reason == ""


def test_decision_verdict_routes_low_confidence_pointer_to_fix():
    decision = PlannerDecision(
        "t",
        "click",
        "Resource Node",
        0.40,
        0.55,
        0.45,
        "Low-confidence OCR target.",
        target_id="ocr_1",
    )

    verdict = decision_verdict(decision)

    assert verdict.accepted is True
    assert verdict.execution_ready is False
    assert verdict.requires_manual_fix is True
    assert verdict.rejection_reason == "confidence_below_threshold:0.450<0.700"


def test_decision_verdict_rejects_low_confidence_key_without_fix():
    decision = PlannerDecision.from_mapping(
        {
            "action_type": "key",
            "key_name": "escape",
            "confidence": 0.2,
            "reason": "Try escape.",
        }
    )

    verdict = decision_verdict(decision)

    assert verdict.accepted is False
    assert verdict.requires_manual_fix is False
    assert verdict.rejection_reason == "confidence_below_threshold:0.200<0.700"


def test_decision_requires_manual_fix_when_source_is_ai_review():
    decision = PlannerDecision(
        "t",
        "click",
        "Resource Node",
        0.30,
        0.40,
        0.91,
        "Manual review requested.",
        source="ai_review",
        target_id="ocr_1",
    )

    assert decision_requires_manual_fix(decision) is True
