from types import SimpleNamespace

from runtime_composition import SupervisorRuntimeComposition


def test_runtime_composition_build_bot_reuses_shared_collaborators():
    fake_config = SimpleNamespace(get=lambda _key, default=None: default)
    fake_signal_emitter = object()
    fake_window_handler = object()
    fake_detector = object()
    composition = SupervisorRuntimeComposition(
        "Test Window",
        delay=0.5,
        config=fake_config,
        signal_emitter=fake_signal_emitter,
        window_handler=fake_window_handler,
        detector=fake_detector,
        vision_memory=object(),
        detection_dataset=object(),
    )

    bot = composition.build_bot()

    assert bot.config is fake_config
    assert bot.signal_emitter is fake_signal_emitter
    assert bot.window_handler is fake_window_handler
    assert bot.detector is fake_detector


def test_runtime_composition_context_wires_runtime_factories():
    fake_config = SimpleNamespace(get=lambda _key, default=None: default)
    fake_window_handler = object()
    fake_detector = object()
    composition = SupervisorRuntimeComposition(
        "Test Window",
        config=fake_config,
        signal_emitter=object(),
        window_handler=fake_window_handler,
        detector=fake_detector,
        vision_memory=object(),
        detection_dataset=object(),
    )

    context = composition.create_context(bot=object(), planner_goal="Gather safely", planner_autonomy_level=2)
    state_monitor = context.build_state_monitor()
    recovery_executor = context.build_recovery_executor()

    assert context.build_window_handler() is fake_window_handler
    assert context.build_config() is fake_config
    assert state_monitor.window_handler is fake_window_handler
    assert state_monitor.config is fake_config
    assert state_monitor._detector is fake_detector
    assert recovery_executor.detector is fake_detector
    assert context.planner_goal == "Gather safely"
    assert context.planner_autonomy_level == 2
