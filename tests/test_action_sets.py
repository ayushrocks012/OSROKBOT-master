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
