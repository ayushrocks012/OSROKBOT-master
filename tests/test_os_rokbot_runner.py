import threading
import time

from OS_ROKBOT import OSROKBOT, EmergencyStop


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