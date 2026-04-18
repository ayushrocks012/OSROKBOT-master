import asyncio
import math
import threading
from types import SimpleNamespace

import dynamic_planner as dynamic_planner_module
import pytest
from dynamic_planner import AsyncPlannerTransport, DynamicPlanner, PlannerDecision, PlannerLLMDecision
from encoding_utils import safe_json_loads
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


class _FakeTransport:
    def __init__(self, request):
        self._request = request
        self.payloads = []

    def request(self, request_payload, should_cancel):
        self.payloads.append(request_payload)
        return self._request(request_payload, should_cancel)

    def close(self):
        return None


def _planner_with_transport(request):
    planner = DynamicPlanner(config=_FakeConfig(), memory=_FakeMemory())
    planner._transport = _FakeTransport(request)
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
        '{"thought_process":"Need to gather.","action_type":"click","target_id":"det_1",'
        '"label":"Gather Button","confidence":0.91,"delay_seconds":1.0,'
        '"reason":"The gather button is visible."}'
    )


def _low_confidence_click_response():
    return _FakeResponse(
        '{"thought_process":"Could be a resource.","action_type":"click","target_id":"det_1",'
        '"label":"Resource node","confidence":0.46,"delay_seconds":1.0,'
        '"reason":"The resource node appears visible.",'
        '"end_target_id":"","key_name":"","text_content":"","drag_direction":""}'
    )


def _stop_response():
    return _FakeResponse(
        '{"thought_process":"No safe detector target is visible.","action_type":"stop","target_id":"",'
        '"label":"stop","confidence":0.91,"delay_seconds":0.0,'
        '"reason":"No listed target is safe.",'
        '"end_target_id":"","key_name":"","text_content":"","drag_direction":""}'
    )


def test_safe_json_loads_accepts_wrapped_json():
    raw = 'assistant says: {"action_type": "wait", "label": "none"} done'

    parsed = safe_json_loads(raw)

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
                "thought_process": "Need to teleport.",
                "action_type": "teleport",
                "target_id": "det_1",
                "label": "Map",
                "confidence": 0.91,
                "delay_seconds": 1.0,
                "reason": "Teleport is not allowed.",
            }
        )


@pytest.mark.parametrize(
    "decision",
    [
        PlannerDecision("t", "click", "bad", -0.1, 0.5, 0.9, "x is outside"),
        PlannerDecision("t", "click", "bad", 0.5, 1.1, 0.9, "y is outside"),
        PlannerDecision("t", "click", "bad", 0.5, 0.5, 0.1, "low confidence"),
        PlannerDecision("t", "teleport", "bad", 0.5, 0.5, 0.9, "unsupported action"),
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
    assert DynamicPlanner.decision_rejection_reason(decision) == "missing_target_id"


def test_decision_rejection_reason_reports_low_confidence():
    decision = PlannerDecision("t", "click", "resource", 0.5, 0.5, 0.5, "uncertain", target_id="ocr_1")

    assert DynamicPlanner.validate_decision(decision) is False
    assert DynamicPlanner.decision_rejection_reason(decision) == "confidence_below_threshold:0.500<0.700"


def test_validate_decision_allows_safe_wait_without_coordinates():
    decision = PlannerDecision("t", "wait", "wait", math.nan, math.nan, 0.0, "No safe click.")

    assert DynamicPlanner.validate_decision(decision) is True


def test_planner_decision_delay_seconds_is_clamped():
    high = PlannerDecision.from_mapping({"action_type": "wait", "delay_seconds": 99})
    low = PlannerDecision.from_mapping({"action_type": "wait", "delay_seconds": -3})

    assert high.delay_seconds == pytest.approx(10.0)
    assert low.delay_seconds == pytest.approx(0.0)


def test_async_planner_transport_retries_transient_failures(monkeypatch):
    calls = []

    async def create_response(_request_payload):
        calls.append("call")
        if len(calls) == 1:
            raise RuntimeError("transient")
        return _click_response()

    monkeypatch.setattr(dynamic_planner_module, "RETRY_BASE_DELAY_SECONDS", 0.0)
    transport = AsyncPlannerTransport(
        api_key="test-key",
        is_transient_error=lambda exc: isinstance(exc, RuntimeError),
        poll_seconds=0.005,
    )
    monkeypatch.setattr(transport, "_create_response", create_response)
    response = transport.request({"model": "gpt-5.4-mini"}, lambda: False)
    transport.close()

    assert len(calls) == 2
    assert response is not None


def test_request_decision_uses_transport_and_parses_response(tmp_path):
    planner = _planner_with_transport(lambda _payload, _should_cancel: _click_response())

    decision = planner._request_decision(
        SimpleNamespace(state_history=[]),
        _screen_path(tmp_path),
        detections=[],
        ocr_text="",
        goal="Gather resources.",
        targets=_target(),
    )

    assert decision is not None
    assert decision.target_id == "det_1"
    assert decision.x == pytest.approx(0.25)
    response_format = planner._transport.payloads[0]["text"]["format"]
    assert response_format["strict"] is True
    assert set(response_format["schema"]["required"]) == set(response_format["schema"]["properties"])
    assert "end_target_id" in response_format["schema"]["required"]


def test_openai_schema_removes_pydantic_defaults():
    schema_text = str(DynamicPlanner.SCHEMA)

    assert "'default'" not in schema_text
    assert DynamicPlanner.SCHEMA["additionalProperties"] is False
    assert set(DynamicPlanner.SCHEMA["required"]) == set(DynamicPlanner.SCHEMA["properties"])


def test_request_decision_returns_none_on_terminal_transport_error(tmp_path):
    def request(_payload, _should_cancel):
        raise ValueError("terminal")

    planner = _planner_with_transport(request)

    decision = planner._request_decision(
        SimpleNamespace(state_history=[]),
        _screen_path(tmp_path),
        detections=[],
        ocr_text="",
        goal="Gather resources.",
        targets=_target(),
    )

    assert decision is None


def test_request_decision_records_timing(tmp_path):
    timings = []
    planner = _planner_with_transport(lambda _payload, _should_cancel: _click_response())
    context = SimpleNamespace(
        state_history=[],
        record_runtime_timing=lambda stage, duration_ms, detail="": timings.append((stage, duration_ms, detail)),
    )

    decision = planner._request_decision(
        context,
        _screen_path(tmp_path),
        detections=[],
        ocr_text="",
        goal="Gather resources.",
        targets=_target(),
    )

    assert decision is not None
    assert timings
    assert timings[-1][0] == "planner_request"
    assert timings[-1][2] == "action=click"
    assert timings[-1][1] >= 0.0


def test_plan_next_routes_low_confidence_pointer_to_l1_review(tmp_path):
    planner = _planner_with_transport(lambda _payload, _should_cancel: _low_confidence_click_response())
    context = SimpleNamespace(state_history=[], planner_autonomy_level=1)

    decision = planner.plan_next(
        context,
        _screen_path(tmp_path),
        detections=[
            {
                "label": "Resource node",
                "x": 0.25,
                "y": 0.75,
                "width": 0.10,
                "height": 0.20,
                "confidence": 0.88,
            }
        ],
        ocr_text="",
        goal="Gather resources.",
    )

    assert decision is not None
    assert decision.source == "ai_review"
    assert decision.confidence == pytest.approx(0.46)


def test_plan_next_routes_stop_without_detections_to_ocr_fix_review(tmp_path):
    planner = _planner_with_transport(lambda _payload, _should_cancel: _stop_response())
    context = SimpleNamespace(state_history=[], planner_autonomy_level=1)

    decision = planner.plan_next(
        context,
        _screen_path(tmp_path),
        detections=[],
        ocr_text="4 wood node",
        goal="Gather the nearest useful resource safely.",
        ocr_regions=[
            {"text": "Technology Research", "x": 0.08, "y": 0.30, "width": 0.18, "height": 0.12, "confidence": 0.90},
            {"text": "4", "x": 0.34, "y": 0.78, "width": 0.04, "height": 0.04, "confidence": 0.62},
        ],
    )

    assert decision is not None
    assert decision.source == "ai_review"
    assert decision.action_type == "click"
    assert decision.target_id == "ocr_2"
    assert "Use Fix" in decision.reason
    assert 0.10 <= decision.confidence < 0.70


def test_plan_next_keeps_stop_without_detections_for_non_resource_goal(tmp_path):
    planner = _planner_with_transport(lambda _payload, _should_cancel: _stop_response())
    context = SimpleNamespace(state_history=[], planner_autonomy_level=1)

    decision = planner.plan_next(
        context,
        _screen_path(tmp_path),
        detections=[],
        ocr_text="4 wood node",
        goal="Open alliance mail safely.",
        ocr_regions=[
            {"text": "4", "x": 0.34, "y": 0.78, "width": 0.04, "height": 0.04, "confidence": 0.62},
        ],
    )

    assert decision is not None
    assert decision.action_type == "stop"


def test_plan_next_prefers_memory_when_entry_resolves_to_valid_target(tmp_path):
    class _Memory:
        def find(self, *_args, **_kwargs):
            return {
                "label": "Gather Button",
                "normalized_point": {"x": 0.25, "y": 0.75},
                "confidence": 0.95,
                "action_type": "click",
                "similarity": 0.98,
            }

    planner = DynamicPlanner(config=_FakeConfig(), memory=_Memory())
    planner._transport = _FakeTransport(lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("transport should not run")))
    timings = []
    context = SimpleNamespace(
        state_history=[],
        record_runtime_timing=lambda stage, duration_ms, detail="": timings.append((stage, duration_ms, detail)),
    )

    decision = planner.plan_next(
        context,
        _screen_path(tmp_path),
        detections=[
            {
                "label": "Gather Button",
                "x": 0.25,
                "y": 0.75,
                "width": 0.10,
                "height": 0.20,
                "confidence": 0.88,
            }
        ],
        ocr_text="",
        goal="Gather resources.",
    )

    assert decision is not None
    assert decision.source == "memory"
    assert decision.target_id == "det_1"
    assert ("planner_memory_lookup", timings[0][1], "hit") == timings[0]


def test_build_targets_includes_detector_and_ocr_regions():
    targets = DynamicPlanner.build_targets(
        detections=[{"label": "Confirm", "x": 0.20, "y": 0.30, "width": 0.10, "height": 0.10, "confidence": 0.90}],
        ocr_regions=[{"text": "Search", "x": 0.60, "y": 0.70, "width": 0.15, "height": 0.10, "confidence": 0.80}],
    )

    assert [target.target_id for target in targets] == ["det_1", "ocr_1"]
    assert targets[1].label == "Search"


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (
            PlannerDecision("t", "drag", "map", 0.2, 0.3, 0.9, "drag", target_id="det_1", drag_direction="left"),
            True,
        ),
        (
            PlannerDecision("t", "key", "escape", math.nan, math.nan, 0.9, "key", key_name="escape"),
            True,
        ),
        (
            PlannerDecision("t", "type", "chat", math.nan, math.nan, 0.9, "type", text_content="hello"),
            True,
        ),
        (
            PlannerDecision("t", "drag", "map", 0.2, 0.3, 0.5, "drag", target_id="det_1", drag_direction="left"),
            False,
        ),
    ],
)
def test_validate_decision_handles_extended_actions(decision, expected):
    assert DynamicPlanner.validate_decision(decision) is expected


def test_async_planner_transport_returns_when_paused_while_future_is_pending(monkeypatch):
    pause_event = threading.Event()
    started = threading.Event()

    async def create_response(_request_payload):
        started.set()
        pause_event.set()
        await asyncio.sleep(0.05)
        return _click_response()

    transport = AsyncPlannerTransport(
        api_key="test-key",
        is_transient_error=lambda _exc: False,
        poll_seconds=0.005,
    )
    monkeypatch.setattr(transport, "_create_response", create_response)
    decision = transport.request({"model": "gpt-5.4-mini"}, lambda: pause_event.is_set())
    assert started.is_set()
    transport.close()

    assert decision is None
