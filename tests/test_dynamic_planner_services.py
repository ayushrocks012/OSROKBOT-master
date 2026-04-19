from types import SimpleNamespace

from Actions.dynamic_planner_services import PlannerExecutionService
from dynamic_planner import PlannerDecision


class _WindowRect:
    left = 0
    top = 0
    width = 400
    height = 200


class _FakeInputController:
    def __init__(self):
        self.calls = []

    def validate_bounds(self, x, y, window_rect):
        self.calls.append(("validate_bounds", x, y, window_rect.width))
        return True

    def click(self, x, y, window_rect=None, remember_position=True, context=None):
        self.calls.append(("click", x, y, remember_position, window_rect.width))
        return True


def test_planner_execution_service_uses_context_input_controller_factory():
    controller = _FakeInputController()
    context = SimpleNamespace(build_input_controller=lambda: controller)
    decision = PlannerDecision.from_mapping(
        {
            "action_type": "click",
            "target_id": "det_1",
            "label": "Gather Button",
            "confidence": 0.91,
            "reason": "Visible target.",
            "x": 0.25,
            "y": 0.5,
        }
    )

    result = PlannerExecutionService().execute(context, decision, _WindowRect())

    assert result is True
    assert controller.calls == [
        ("validate_bounds", 100, 100, 400),
        ("click", 100, 100, False, 400),
    ]


def test_planner_execution_service_records_runtime_journal_boundaries():
    controller = _FakeInputController()
    journal_calls = []
    scope_updates = []

    class _SessionLogger:
        def record_input_started(self, **kwargs):
            journal_calls.append(("started", kwargs))
            return "input_1"

        def record_input_completed(self, **kwargs):
            journal_calls.append(("completed", kwargs))

    context = SimpleNamespace(
        build_input_controller=lambda: controller,
        session_logger=_SessionLogger(),
        active_step_scope=lambda: {
            "step_id": "step_1",
            "machine_id": "machine_1",
            "state_name": "gather",
            "decision_id": "decision_1",
        },
        update_active_step_scope=lambda **kwargs: scope_updates.append(kwargs),
    )
    decision = PlannerDecision.from_mapping(
        {
            "action_type": "click",
            "target_id": "det_1",
            "label": "Gather Button",
            "confidence": 0.91,
            "reason": "Visible target.",
            "x": 0.25,
            "y": 0.5,
        }
    )

    result = PlannerExecutionService().execute(context, decision, _WindowRect())

    assert result is True
    assert journal_calls[0][0] == "started"
    assert journal_calls[0][1]["decision_id"] == "decision_1"
    assert journal_calls[1][0] == "completed"
    assert journal_calls[1][1]["outcome"] == "success"
    assert scope_updates == [{"decision_id": None, "approval_id": None, "input_id": "input_1"}, {"decision_id": None, "approval_id": None, "input_id": None}]
