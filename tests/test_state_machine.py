import sys
from types import ModuleType

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


def test_state_machine_close_closes_each_unique_action_once():
    class ClosableAction(DummyAction):
        def __init__(self):
            super().__init__(True)
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    action = ClosableAction()
    machine = StateMachine()
    machine.add_state("start", action, next_state_on_success="finish")
    machine.add_state("finish", action, next_state_on_success="finish")

    machine.close()

    assert action.close_calls == 1


def test_state_machine_execute_records_success_transition():
    machine = StateMachine()
    machine.add_state("start", DummyAction(True), next_state_on_success="finish")
    machine.add_state("finish", DummyAction(True), next_state_on_success="finish")
    machine.set_initial_state("start")
    context = RecordingContext()

    assert machine.execute(context) is True
    assert machine.current_state == "finish"
    assert context.records[-1][0][0] == "start"
    assert context.records[-1][1]["next_state"] == "finish"


def test_state_machine_precondition_failure_uses_fallback_state():
    machine = StateMachine()
    machine.add_state(
        "start",
        DummyAction(True),
        next_state_on_success="finish",
        next_state_on_failure="start",
        precondition=lambda _context: False,
        fallback_state="repair",
    )
    machine.add_state("repair", DummyAction(True), next_state_on_success="finish")
    machine.set_initial_state("start")
    context = RecordingContext()

    assert machine.execute(context) is False
    assert machine.current_state == "repair"
    assert context.records[-1][1]["event"] == "precondition"


def test_state_machine_repeated_precondition_failures_trigger_global_recovery(monkeypatch):
    machine = StateMachine()
    machine.precondition_recovery_threshold = 2
    machine.add_state(
        "start",
        DummyAction(True),
        next_state_on_success="finish",
        precondition=lambda _context: False,
    )
    machine.set_initial_state("start")
    context = RecordingContext()
    diagnostics = []
    recoveries = []
    context.save_failure_diagnostic = diagnostics.append
    monkeypatch.setattr(machine, "global_recovery", lambda ctx=None: recoveries.append(ctx) or True)

    assert machine.execute(context) is False
    assert machine.execute(context) is False
    assert diagnostics == ["precondition_start"]
    assert recoveries == [context]


def test_state_machine_failed_actions_trigger_guarded_recovery(monkeypatch):
    class RecoverableAction(DummyAction):
        image = "confirm.png"

    machine = StateMachine()
    machine.ai_fallback_threshold = 1
    machine.add_state("start", RecoverableAction(False), next_state_on_success="finish", next_state_on_failure="start")
    machine.set_initial_state("start")
    context = RecordingContext()
    diagnostics = []
    context.save_failure_diagnostic = lambda state_name: diagnostics.append(state_name) or "screen.png"
    monkeypatch.setattr(machine, "global_recovery", lambda ctx=None: False)
    guarded = []
    monkeypatch.setattr(machine, "_run_guarded_recovery", lambda *args: guarded.append(args) or True)

    assert machine.execute(context) is False
    assert diagnostics == ["start"]
    assert len(guarded) == 1


def test_recovery_close_menus_returns_true_after_known_state():
    class _State:
        def __init__(self, value):
            self.value = value

    class _GameState:
        CITY = _State("CITY")
        MAP = _State("MAP")
        UNKNOWN = _State("UNKNOWN")

    monitor = type("Monitor", (), {"clear_blockers": lambda self: None, "current_state": lambda self: _GameState.CITY})()
    controller = type("Controller", (), {"key_press": lambda self, *args, **kwargs: True, "wait": lambda self, *args, **kwargs: True})()

    assert StateMachine()._recovery_close_menus(monitor, controller, None, _GameState) is True


def test_global_recovery_uses_first_successful_tier(monkeypatch):
    class _State:
        def __init__(self, value):
            self.value = value

    class _GameState:
        CITY = _State("CITY")
        MAP = _State("MAP")
        UNKNOWN = _State("UNKNOWN")

    fake_input_controller = ModuleType("input_controller")
    fake_input_controller.InputController = lambda context=None: object()
    fake_state_monitor = ModuleType("state_monitor")
    fake_state_monitor.GameState = _GameState
    fake_state_monitor.GameStateMonitor = lambda context=None: type(
        "Monitor",
        (),
        {"save_diagnostic_screenshot": lambda self, _label: None},
    )()
    fake_window_handler = ModuleType("window_handler")
    fake_window_handler.WindowHandler = lambda: type("Handler", (), {"ensure_foreground": lambda self, _title, wait_seconds=0.5: True})()
    monkeypatch.setitem(sys.modules, "input_controller", fake_input_controller)
    monkeypatch.setitem(sys.modules, "state_monitor", fake_state_monitor)
    monkeypatch.setitem(sys.modules, "window_handler", fake_window_handler)
    machine = StateMachine()
    machine.current_state = "start"
    monkeypatch.setattr(machine, "_recovery_close_menus", lambda *args: True)
    monkeypatch.setattr(machine, "_recovery_toggle_view", lambda *args: False)
    monkeypatch.setattr(machine, "_recovery_restart_game", lambda *args: False)

    assert machine.global_recovery() is True


def test_should_run_guarded_recovery_skips_captcha_and_requires_threshold():
    machine = StateMachine()

    assert machine._should_run_guarded_recovery(type("Action", (), {"image": "confirm.png"})(), 1) is False
    assert machine._should_run_guarded_recovery(type("Action", (), {"image": "captcha.png"})(), 3) is False
    assert machine._should_run_guarded_recovery(type("Action", (), {"image": "confirm.png"})(), 3) is True


def test_get_recovery_executor_returns_none_when_import_fails(monkeypatch):
    original_import = __import__

    def failing_import(name, *args, **kwargs):
        if name == "ai_recovery_executor":
            raise ImportError("missing recovery")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", failing_import)

    assert StateMachine()._get_recovery_executor() is None


def test_verify_pending_recovery_uses_executor(monkeypatch):
    machine = StateMachine()
    calls = []
    machine.recovery_executor = type("Executor", (), {"verify_pending": lambda self, *args: calls.append(args)})()
    context = object()

    machine._verify_pending_recovery(context, "start", "finish", True)

    assert calls == [(context, "start", "finish", True)]


def test_precondition_passes_accepts_action_callable_and_boolean():
    action = type("Action", (), {"perform": lambda self, context=None: True})()

    assert StateMachine._precondition_passes(action) is True
    assert StateMachine._precondition_passes(lambda _context: True) is True
    assert StateMachine._precondition_passes(False) is False


def test_emit_recovery_state_uses_context_emitter():
    emitted = []
    context = type("Context", (), {"emit_state": lambda self, text: emitted.append(text)})()

    StateMachine._emit_recovery_state(context, "Recovery state")

    assert emitted == ["Recovery state"]


def test_recovery_toggle_view_succeeds_after_unknown_state(monkeypatch):
    class _State:
        def __init__(self, value):
            self.value = value

    class _GameState:
        CITY = _State("CITY")
        MAP = _State("MAP")
        UNKNOWN = _State("UNKNOWN")

    states = iter([_GameState.UNKNOWN, _GameState.MAP])
    monitor = type("Monitor", (), {"clear_blockers": lambda self: None, "current_state": lambda self: next(states)})()
    controller = type("Controller", (), {"key_press": lambda self, *args, **kwargs: True, "wait": lambda self, *args, **kwargs: True})()

    assert StateMachine()._recovery_toggle_view(monitor, controller, None, _GameState) is True


def test_recovery_restart_game_returns_false_when_client_restart_fails():
    class _State:
        def __init__(self, value):
            self.value = value

    class _GameState:
        CITY = _State("CITY")
        MAP = _State("MAP")
        UNKNOWN = _State("UNKNOWN")

    monitor = type(
        "Monitor",
        (),
        {"current_state": lambda self: _GameState.UNKNOWN, "restart_client": lambda self: False},
    )()
    controller = type("Controller", (), {"wait": lambda self, *args, **kwargs: True})()

    assert StateMachine()._recovery_restart_game(monitor, controller, None, _GameState) is False
