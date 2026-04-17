import json
import threading
import time

import pytest

import OS_ROKBOT as os_rokbot
from OS_ROKBOT import OSROKBOT, EmergencyStop
from context import Context


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _allow_start(monkeypatch, bot):
    monkeypatch.setattr(bot, "_hardware_input_ready", lambda context=None: True)
    monkeypatch.setattr(EmergencyStop, "start_once", staticmethod(lambda: True))


def test_write_heartbeat_file_writes_json_atomically(tmp_path):
    heartbeat_path = tmp_path / "nested" / "heartbeat.json"
    payload = {
        "timestamp_epoch": 123.5,
        "bot_pid": 456,
        "game_pid": 789,
        "window_title": "Test Window",
    }

    OSROKBOT._write_heartbeat_file(heartbeat_path, payload)

    assert json.loads(heartbeat_path.read_text(encoding="utf-8")) == payload
    assert not heartbeat_path.with_suffix(heartbeat_path.suffix + ".tmp").exists()


def test_write_heartbeat_file_raises_after_repeated_file_locks(monkeypatch, tmp_path):
    heartbeat_path = tmp_path / "heartbeat.json"
    replace_calls = []

    def locked_replace(self, target):
        replace_calls.append((self, target))
        raise PermissionError("locked")

    monkeypatch.setattr(type(heartbeat_path), "replace", locked_replace)
    monkeypatch.setattr(os_rokbot.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError):
        OSROKBOT._write_heartbeat_file(heartbeat_path, {"timestamp_epoch": 1.0})

    assert len(replace_calls) == 3


def test_game_pid_cache_reuses_pid_and_clears_when_window_missing(monkeypatch):
    class FakeWindow:
        def __init__(self, hwnd):
            self._hWnd = hwnd

    class FakeWindowHandler:
        def __init__(self):
            self.window = FakeWindow(100)

        def get_window(self, _title):
            return self.window

    class FakeWin32Process:
        def __init__(self):
            self.calls = 0

        def GetWindowThreadProcessId(self, hwnd):
            self.calls += 1
            return 0, hwnd + 1000

    fake_window_handler = FakeWindowHandler()
    fake_win32process = FakeWin32Process()
    monkeypatch.setattr(os_rokbot, "win32process", fake_win32process)

    bot = OSROKBOT("Test Window", delay=0)
    bot.window_handler = fake_window_handler

    assert bot._game_pid("Test Window") == 1100
    assert bot._game_pid("Test Window") == 1100
    assert fake_win32process.calls == 1

    fake_window_handler.window = FakeWindow(200)
    assert bot._game_pid("Test Window") == 1200
    assert fake_win32process.calls == 2

    fake_window_handler.window = None
    assert bot._game_pid("Test Window") is None
    assert bot._cached_game_pid is None

    fake_window_handler.window = FakeWindow(200)
    assert bot._game_pid("Test Window") == 1200
    assert fake_win32process.calls == 3


def test_start_creates_fresh_runner_executor_per_run(monkeypatch):
    bot = OSROKBOT("Test Window", delay=0)
    _allow_start(monkeypatch, bot)
    release = threading.Event()
    started = threading.Event()

    def blocking_run(_steps, context=None):
        started.set()
        release.wait(2)

    monkeypatch.setattr(bot, "run", blocking_run)

    assert bot.start(["first"]) is True
    assert started.wait(2)
    first_executor = bot._runner_executor
    first_future = bot._runner_future
    release.set()
    first_future.result(timeout=2)
    assert _wait_for(lambda: bot._runner_executor is None)
    assert bot.all_threads_joined is True

    release.clear()
    started.clear()
    assert bot.start(["second"]) is True
    assert started.wait(2)
    second_executor = bot._runner_executor
    second_future = bot._runner_future
    assert second_executor is not None
    assert second_executor is not first_executor
    release.set()
    second_future.result(timeout=2)
    assert _wait_for(lambda: bot._runner_executor is None)


def test_stop_shuts_down_runner_executor_without_queueing_second_run(monkeypatch):
    bot = OSROKBOT("Test Window", delay=0)
    _allow_start(monkeypatch, bot)
    started = threading.Event()

    def blocking_run(_steps, context=None):
        bot.all_threads_joined = False
        started.set()
        bot.stop_event.wait(2)

    monkeypatch.setattr(bot, "run", blocking_run)

    assert bot.start(["first"]) is True
    assert started.wait(2)
    assert bot.start(["queued"]) is False
    future = bot._runner_future
    bot.stop()

    assert bot._runner_executor is None
    assert bot.is_running is False
    future.result(timeout=2)
    assert _wait_for(lambda: bot.all_threads_joined is True)


def test_run_reuses_single_observation_for_captcha_and_execute(monkeypatch):
    class FakeWindowRect:
        left = 0
        top = 0
        width = 400
        height = 200

    class FakeWindowHandler:
        def __init__(self):
            self.calls = 0

        def screenshot_window(self, _title):
            self.calls += 1
            return "screen", FakeWindowRect()

    class FakeDetector:
        def __init__(self):
            self.calls = 0

        def detect(self, screenshot):
            self.calls += 1
            assert screenshot == "screen"
            return [type("Detection", (), {"label": "map"})()]

    observed = {}

    class FakeMachine:
        halted = False

        def execute(self, context):
            observed["observation"] = context.current_observation
            context.bot.stop_event.set()
            return False

    bot = OSROKBOT("Test Window", delay=0)
    bot.window_handler = FakeWindowHandler()
    bot.detector = FakeDetector()
    monkeypatch.setattr(bot, "_ensure_foreground", lambda _context: True)
    monkeypatch.setattr(bot, "write_heartbeat", lambda _context=None, force=False: True)

    context = Context(bot=bot, window_title="Test Window")
    bot.run([FakeMachine()], context)

    assert bot.window_handler.calls == 1
    assert bot.detector.calls == 1
    assert observed["observation"] is not None
    assert observed["observation"].screenshot == "screen"
    assert len(observed["observation"].detections) == 1
    assert context.current_observation is None
