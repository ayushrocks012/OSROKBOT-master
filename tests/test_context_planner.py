from context import Context
from dynamic_planner import PlannerDecision


class _RecorderSignal:
    def __init__(self):
        self.payloads = []

    def emit(self, payload):
        self.payloads.append(payload)


class _FakeEmitter:
    def __init__(self):
        self.state_changed = _RecorderSignal()
        self.planner_decision = _RecorderSignal()


class _WindowRect:
    left = 100
    top = 50
    width = 400
    height = 200


def test_context_runtime_factories_return_injected_collaborators():
    fake_window_handler = object()
    fake_controller = object()
    fake_monitor = object()
    calls = []
    context = Context(
        window_handler_factory=lambda: fake_window_handler,
        input_controller_factory=lambda runtime_context: calls.append(("input", runtime_context)) or fake_controller,
        state_monitor_factory=lambda runtime_context: calls.append(("monitor", runtime_context)) or fake_monitor,
    )

    assert context.build_window_handler() is fake_window_handler
    assert context.build_input_controller() is fake_controller
    assert context.build_state_monitor() is fake_monitor
    assert calls == [("input", context), ("monitor", context)]


def test_pending_planner_decision_emits_absolute_coordinates():
    emitter = _FakeEmitter()
    context = Context(signal_emitter=emitter)
    decision = PlannerDecision("t", "click", "Gather Button", 0.25, 0.50, 0.9, "Visible.", target_id="det_1")
    detections = [
        {
            "label": "Gather Button",
            "x": 0.25,
            "y": 0.50,
            "width": 0.10,
            "height": 0.20,
            "confidence": 0.9,
        }
    ]

    pending = context.set_pending_planner_decision(
        decision,
        screenshot_path="screen.png",
        window_rect=_WindowRect(),
        detections=detections,
    )

    assert pending["absolute_x"] == 200
    assert pending["absolute_y"] == 150
    assert pending["detections"][0]["target_id"] == "det_1"
    assert emitter.state_changed.payloads == ["Planner approval needed"]
    assert emitter.planner_decision.payloads[0]["absolute_x"] == 200
    assert emitter.planner_decision.payloads[0]["decision"]["target_id"] == "det_1"
    assert emitter.planner_decision.payloads[0]["decision"]["label"] == "Gather Button"
    assert emitter.planner_decision.payloads[0]["detections"][0]["width"] == 0.10


def test_resolve_planner_decision_records_rejection_and_unblocks_event():
    context = Context()
    decision = PlannerDecision("t", "click", "Gather Button", 0.25, 0.50, 0.9, "Visible.")
    pending = context.set_pending_planner_decision(decision)

    assert context.resolve_planner_decision(False) is True

    assert pending["result"] == "rejected"
    assert pending["event"].is_set()


def test_current_observation_clear_is_identity_guarded():
    context = Context()
    first = context.set_current_observation("screen-1", _WindowRect())
    second = context.set_current_observation("screen-2", _WindowRect())

    assert context.clear_current_observation_if(first) is False
    assert context.get_current_observation() is second
    assert context.clear_current_observation_if(second) is True
    assert context.get_current_observation() is None


def test_record_state_trims_history_to_max_entries():
    context = Context(max_state_history=2)

    context.record_state("s1", "a1", True, next_state="s2")
    context.record_state("s2", "a2", False, next_state="s3")
    context.record_state("s3", "a3", True, next_state="s4")

    assert [entry["state"] for entry in context.state_history] == ["s2", "s3"]


def test_ui_anchor_relative_point_uses_anchor_reference():
    context = Context()
    context.set_ui_anchor("primary", 200, 100, _WindowRect(), reference_normalized=(0.25, 0.25))

    resolved = context.resolve_anchor_relative_point(0.50, 0.50, _WindowRect())

    assert resolved == (300, 150)


def test_export_state_history_writes_flat_text_log(tmp_path):
    context = Context()
    context.record_state("gather", "click\nconfirm", True, next_state="done")
    output_path = tmp_path / "history.log"

    written = context.export_state_history(output_path)

    assert written == output_path
    assert "state=gather" in output_path.read_text(encoding="utf-8")
    assert "click | confirm" in output_path.read_text(encoding="utf-8")


def test_set_extracted_text_updates_named_fields_and_sanitizes_text():
    context = Context()

    context.set_extracted_text("Q", 'Question, "quoted"')
    context.set_extracted_text("note", 'Value, "quoted"')

    assert context.Q == "Question quoted"
    assert context.extracted["note"] == "Value quoted"


def test_record_runtime_timing_updates_context_and_session_logger():
    recorded = []
    session_logger = type(
        "Logger",
        (),
        {"record_timing": lambda self, stage, duration_ms, detail="": recorded.append((stage, duration_ms, detail))},
    )()
    context = Context(session_logger=session_logger, max_runtime_timing_history=2)

    context.record_runtime_timing("window_capture", 12.34, detail="title=Rise of Kingdoms")
    context.record_runtime_timing("planner_request", 45.67, detail="action=click")
    context.record_runtime_timing("input_execute", 7.89, detail="action=click result=True")

    assert [entry["stage"] for entry in context.runtime_timing_history] == [
        "planner_request",
        "input_execute",
    ]
    assert context.extracted["runtime_timings"][-1]["detail"] == "action=click result=True"
    assert [item[0] for item in recorded] == [
        "window_capture",
        "planner_request",
        "input_execute",
    ]
