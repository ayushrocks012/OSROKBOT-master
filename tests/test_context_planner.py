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
