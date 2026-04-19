from action_sets import ActionSets


class _FakeAction:
    status_text = "fake"

    def perform(self, context=None):
        return True


def test_dynamic_planner_uses_injected_factory():
    created = []

    def factory():
        action = _FakeAction()
        created.append(action)
        return action

    machine = ActionSets(OS_ROKBOT=object(), dynamic_planner_factory=factory).dynamic_planner()

    assert len(created) == 1
    assert machine.states["plan_next"].action is created[0]
    assert machine.current_state == "plan_next"


def test_map_view_precondition_uses_context_monitor_factory():
    context = type(
        "Context",
        (),
        {"build_state_monitor": lambda self: type("Monitor", (), {"current_state": lambda _self: "MAP"})()},
    )()

    assert ActionSets.map_view_precondition()(context) is True
