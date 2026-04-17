from types import SimpleNamespace

import pytest
from state_monitor import GameState, GameStateMonitor


def _monitor():
    monitor = object.__new__(GameStateMonitor)
    monitor.context = SimpleNamespace(extracted={})
    monitor.window_handler = SimpleNamespace(get_window=lambda _title: None)
    monitor.input_controller = SimpleNamespace()
    monitor._detector = None
    return monitor


@pytest.mark.integration
def test_current_state_prioritizes_blockers_over_map_labels(monkeypatch):
    monitor = _monitor()
    monkeypatch.setattr(monitor, "_screenshot", lambda: ("screen", "rect"))
    monkeypatch.setattr(monitor, "_detect_labels", lambda _screenshot: {"attackaction", "captcha"})

    assert monitor.current_state() == GameState.BLOCKED


@pytest.mark.integration
def test_clear_blockers_sends_escape_only_when_blocker_detected(monkeypatch):
    monitor = _monitor()
    sent_keys = []
    monitor.input_controller = SimpleNamespace(
        key_press=lambda key, hold_seconds=0.1, context=None: sent_keys.append((key, hold_seconds, context)) or True
    )
    monkeypatch.setattr(monitor, "_screenshot", lambda: ("screen", "rect"))
    monkeypatch.setattr(monitor, "_detect_labels", lambda _screenshot: {"confirm"})

    assert monitor.clear_blockers() is True
    assert sent_keys == [("escape", 0.1, monitor.context)]


@pytest.mark.integration
def test_count_idle_march_slots_parses_and_caches_ocr(monkeypatch):
    monitor = _monitor()
    monkeypatch.setattr(monitor, "_screenshot", lambda: ("screen", "rect"))
    monkeypatch.setattr(monitor, "_extract_roi", lambda screenshot, roi: (screenshot, roi))
    monkeypatch.setattr(monitor, "_ocr_digits", lambda _roi: "2/5")

    assert monitor.count_idle_march_slots(max_age_seconds=30) == 3
    assert monitor.context.idle_march_slots == 3
    assert monitor.context.extracted["idle_march_slots"]["value"] == 3


@pytest.mark.integration
def test_restart_client_rejects_missing_configured_path(monkeypatch):
    monitor = _monitor()
    monitor.context.bot = None

    class FakeConfig:
        def get(self, key):
            assert key == "ROK_CLIENT_PATH"
            return "C:/missing/RiseOfKingdoms.exe"

    monkeypatch.setattr("state_monitor.ConfigManager", lambda: FakeConfig())

    assert monitor.restart_client() is False
