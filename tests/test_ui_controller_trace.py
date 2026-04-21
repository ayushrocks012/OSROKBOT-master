from UIController import _planner_trace_from_state_text, _view_text_from_payload


def test_planner_trace_from_state_text_extracts_reason_and_confidence():
    trace = _planner_trace_from_state_text(
        "Planner: click -> Gather\nReason: Gather button is visible\nConfidence: 91%"
    )

    assert trace.visible is True
    assert trace.decision_text == "click -> Gather"
    assert trace.reason_text == "Gather button is visible"
    assert trace.confidence_text == "91%"


def test_view_text_from_payload_prefers_detector_labels_and_ocr():
    view_text = _view_text_from_payload(
        {
            "visible_labels": ["Map", "Search"],
            "ocr_text": "food wood stone gems",
        }
    )

    assert "Labels: Map, Search" in view_text
    assert "OCR: food wood stone gems" in view_text
