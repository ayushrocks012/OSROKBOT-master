import threading
from types import SimpleNamespace

import input_controller as input_controller_module
import pytest
from input_controller import DelayPolicy, HumanizationProfile, InputController


def _controller(monkeypatch):
    monkeypatch.setattr(InputController, "ensure_interception_ready", staticmethod(lambda: True))
    monkeypatch.setattr(
        input_controller_module,
        "SYS_RANDOM",
        SimpleNamespace(
            uniform=lambda _low, _high: 0,
            gauss=lambda _mu, _sigma: 0,
        ),
    )
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


def test_humanization_profile_samples_stay_within_bounds(monkeypatch):
    monkeypatch.setattr(
        input_controller_module,
        "SYS_RANDOM",
        SimpleNamespace(
            uniform=lambda _low, _high: 999,
            gauss=lambda _mu, _sigma: 999,
        ),
    )
    profile = HumanizationProfile()

    click_hold = profile.sample_click_hold_seconds()
    long_press = profile.sample_long_press_seconds()
    move_duration = profile.sample_move_duration()

    assert profile.click_hold_bounds[0] <= click_hold <= profile.click_hold_bounds[1]
    assert profile.long_press_bounds[0] <= long_press <= profile.long_press_bounds[1]
    assert profile.move_duration_bounds[0] <= move_duration <= profile.move_duration_bounds[1]


def test_sample_click_target_stays_within_window_bounds(monkeypatch):
    monkeypatch.setattr(InputController, "ensure_interception_ready", staticmethod(lambda: True))
    monkeypatch.setattr(
        input_controller_module,
        "SYS_RANDOM",
        SimpleNamespace(
            uniform=lambda _low, _high: 0,
            gauss=lambda _mu, _sigma: 999,
        ),
    )
    controller = InputController()
    window_rect = SimpleNamespace(left=10, top=20, width=40, height=30)

    sampled_x, sampled_y = controller.sample_click_target(10, 20, window_rect)

    assert InputController.validate_bounds(sampled_x, sampled_y, window_rect) is True


def test_delay_policy_wait_never_sleeps_negative_duration(monkeypatch):
    monotonic_values = iter([0.0, 0.09, 0.11])
    sleep_calls = []

    monkeypatch.setattr(input_controller_module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(input_controller_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    policy = DelayPolicy(default_delay=0.0, poll_delay=0.1, jitter_ratio=0.0)

    assert policy.wait(0.1) is True
    assert sleep_calls == []
