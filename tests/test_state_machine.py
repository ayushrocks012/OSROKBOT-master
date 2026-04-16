from state_machine import StateMachine


class DummyAction:
    status_text = "dummy"

    def __init__(self, result=True):
        self.result = result

    def perform(self, context=None):
        return self.result


class RecordingContext:
    def __init__(self):
        self.extracted = {}
        self.records = []

    def record_state(self, *args, **kwargs):
        self.records.append((args, kwargs))


def test_state_machine_halts_when_success_next_state_resolves_to_none():
    machine = StateMachine()
    machine.add_state("start", DummyAction(True), next_state_on_success=lambda: None, next_state_on_failure="start")
    machine.set_initial_state("start")
    context = RecordingContext()

    assert machine.execute(context) is False
    assert machine.halted is True
    assert machine.current_state == "start"
    assert context.records[-1][1]["next_state"] is None


def test_state_machine_halts_when_precondition_fallback_resolves_to_none():
    machine = StateMachine()
    machine.add_state(
        "start",
        DummyAction(False),
        next_state_on_success="start",
        next_state_on_failure="start",
        precondition=lambda _context: False,
        fallback_state=lambda: None,
    )
    machine.set_initial_state("start")

    assert machine.execute() is False
    assert machine.halted is True
    assert machine.current_state == "start"


def test_state_machine_halts_when_next_state_callable_raises():
    def broken_next_state():
        raise RuntimeError("missing return path")

    machine = StateMachine()
    machine.add_state("start", DummyAction(False), next_state_on_success="start", next_state_on_failure=broken_next_state)
    machine.set_initial_state("start")

    assert machine.execute() is False
    assert machine.halted is True
    assert machine.current_state == "start"