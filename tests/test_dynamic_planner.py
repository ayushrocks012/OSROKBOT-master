import math
import threading
import time
from types import SimpleNamespace

import dynamic_planner as dynamic_planner_module
import pytest
from dynamic_planner import DynamicPlanner, PlannerDecision, PlannerLLMDecision
from pydantic import ValidationError


class _FakeConfig:
    def get(self, _key, default=None):
        return default


class _FakeMemory:
    def find(self, *_args, **_kwargs):
        return None


class _FakeResponse:
    def __init__(self, output_text):
        self.output_text = output_text


def _planner_with_client(create):
    planner = DynamicPlanner(config=_FakeConfig(), memory=_FakeMemory())
    planner.client = SimpleNamespace(responses=SimpleNamespace(create=create))
    return planner


def _screen_path(tmp_path):
    path = tmp_path / "screen.png"
    path.write_bytes(b"fake image bytes")
    return path


def _target():
    return DynamicPlanner.build_targets(
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


def _click_response():
    return _FakeResponse(
        (
            '{"thought_process":"Need to gather.","action_type":"click","target_id":"det_1",'
            '"label":"Gather Button","confidence":0.91,"delay_seconds":1.0,'
            '"reason":"The gather button is visible."}'
        )
    )


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


def test_llm_decision_rejects_raw_coordinates():
    with pytest.raises(ValidationError):
        PlannerLLMDecision.model_validate(
            {
                "thought_process": "Need to gather.",
                "action_type": "click",
                "target_id": "det_1",
                "label": "Gather Button",
                "x": 0.25,
                "confidence": 0.91,
                "delay_seconds": 1.0,
                "reason": "The gather button is visible.",
            }
        )


def test_llm_decision_rejects_unsupported_action_type():
    with pytest.raises(ValidationError):
        PlannerLLMDecision.model_validate(
            {
                "thought_process": "Need to drag.",
                "action_type": "drag",
                "target_id": "det_1",
                "label": "Map",
                "confidence": 0.91,
                "delay_seconds": 1.0,
                "reason": "Drag is not allowed.",
            }
        )


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


def test_request_decision_retries_transient_failures(tmp_path, monkeypatch):
    calls = []

    def create(**_payload):
        calls.append("call")
        if len(calls) == 1:
            raise RuntimeError("transient")
        return _click_response()

    monkeypatch.setattr(dynamic_planner_module, "RETRY_BASE_DELAY_SECONDS", 0.0)
    planner = _planner_with_client(create)
    monkeypatch.setattr(planner, "_is_transient_openai_error", lambda exc: isinstance(exc, RuntimeError))

    decision = planner._request_decision(
        SimpleNamespace(state_history=[]),
        _screen_path(tmp_path),
        detections=[],
        ocr_text="",
        goal="Gather resources.",
        targets=_target(),
    )
    planner._request_executor.shutdown(wait=True)

    assert len(calls) == 2
    assert decision is not None
    assert decision.target_id == "det_1"
    assert decision.x == pytest.approx(0.25)


def test_request_decision_does_not_retry_terminal_errors(tmp_path):
    calls = []

    def create(**_payload):
        calls.append("call")
        raise ValueError("terminal")

    planner = _planner_with_client(create)

    decision = planner._request_decision(
        SimpleNamespace(state_history=[]),
        _screen_path(tmp_path),
        detections=[],
        ocr_text="",
        goal="Gather resources.",
        targets=_target(),
    )
    planner._request_executor.shutdown(wait=True)

    assert decision is None
    assert calls == ["call"]


def test_request_decision_returns_when_paused_while_future_is_pending(tmp_path, monkeypatch):
    pause_event = threading.Event()
    started = threading.Event()

    def create(**_payload):
        started.set()
        pause_event.set()
        time.sleep(0.05)
        return _click_response()

    monkeypatch.setattr(dynamic_planner_module, "REQUEST_POLL_SECONDS", 0.005)
    planner = _planner_with_client(create)
    context = SimpleNamespace(
        state_history=[],
        bot=SimpleNamespace(stop_event=threading.Event(), pause_event=pause_event),
    )

    decision = planner._request_decision(
        context,
        _screen_path(tmp_path),
        detections=[],
        ocr_text="",
        goal="Gather resources.",
        targets=_target(),
    )
    assert started.is_set()
    planner._request_executor.shutdown(wait=True)

    assert decision is None
