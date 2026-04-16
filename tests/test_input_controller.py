import threading
from types import SimpleNamespace

import input_controller as input_controller_module
import pytest
from input_controller import DelayPolicy, InputController


def _controller(monkeypatch):
    monkeypatch.setattr(InputController, "ensure_interception_ready", staticmethod(lambda: True))
    monkeypatch.setattr(input_controller_module.random, "uniform", lambda _low, _high: 0)
    controller = InputController(delay_policy=DelayPolicy(), move_duration=0.5, move_steps_per_second=10)
    controller.delay_policy.wait = lambda _seconds=None, context=None: True
    controller._mouse_position = lambda: (0, 0)
    return controller


def test_ease_in_out_smoothstep_curve():
    assert InputController._ease_in_out(0.0) == pytest.approx(0.0)
    assert InputController._ease_in_out(0.5) == pytest.approx(0.5)
    assert InputController._ease_in_out(1.0) == pytest.approx(1.0)
    assert InputController._ease_in_out(0.25) < 0.25
    assert InputController._ease_in_out(0.75) > 0.75


def test_smooth_move_to_uses_eased_steps_without_hardware(monkeypatch):
    controller = _controller(monkeypatch)
    moves = []
    controller._move_hardware_to = lambda x, y: moves.append((x, y))

    assert controller.smooth_move_to(100, 0, duration=0.5) is True

    assert len(moves) == 5
    assert moves[-1] == (100, 0)


def test_smooth_move_to_checks_interlock_between_steps(monkeypatch):
    controller = _controller(monkeypatch)
    pause_event = threading.Event()
    context = SimpleNamespace(bot=SimpleNamespace(stop_event=threading.Event(), pause_event=pause_event))
    moves = []

    def move_once_then_pause(x, y):
        moves.append((x, y))
        pause_event.set()

    controller._move_hardware_to = move_once_then_pause

    assert controller.smooth_move_to(100, 0, context=context, duration=0.5) is False
    assert len(moves) == 1
