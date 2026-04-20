import sys
from types import ModuleType, SimpleNamespace

import ai_recovery_executor as ai_recovery_executor_module
import pytest
from ai_recovery_executor import AIRecoveryExecutor
from PIL import Image


class _FakeMemory:
    def __init__(self, entry=None):
        self.entry = entry
        self.failure_signatures = []
        self.success_calls = []

    def find(self, signature_parts):
        return self.entry

    def record_failure(self, signature):
        self.failure_signatures.append(signature)

    def record_success(self, *args, **kwargs):
        self.success_calls.append((args, kwargs))


class _FakeContext:
    def __init__(self):
        self.window_title = "Test Window"
        self.state_history = [{"state": "recover"}]
        self.extracted = {}
        self.emitted = []

    def emit_state(self, text):
        self.emitted.append(text)

    @staticmethod
    def resolve_anchor_relative_point(x, y, window_rect):
        return (
            int(window_rect.left + window_rect.width * x),
            int(window_rect.top + window_rect.height * y),
        )


class _FakeAction:
    image = "confirm.png"


class _WindowRect:
    left = 100
    top = 50
    width = 400
    height = 200


def _screen_path(tmp_path):
    screenshot_path = tmp_path / "screen.png"
    Image.new("RGB", (8, 8), "white").save(screenshot_path)
    return screenshot_path


def test_try_recover_uses_memory_hint_and_records_pending_state(monkeypatch, tmp_path):
    exported = []
    memory = _FakeMemory(
        {
            "label": "confirm",
            "normalized_point": {"x": 0.25, "y": 0.50},
            "confidence": 0.95,
        }
    )
    executor = AIRecoveryExecutor(memory=memory, detector=SimpleNamespace())
    context = _FakeContext()
    screenshot_path = _screen_path(tmp_path)
    clicked = []

    monkeypatch.setattr(executor, "_detections", lambda _path: [SimpleNamespace(label="confirm")])
    monkeypatch.setattr(executor, "_click_hint", lambda _context, hint: clicked.append(hint) or True)
    monkeypatch.setattr(
        ai_recovery_executor_module,
        "DetectionDataset",
        lambda: SimpleNamespace(export_stub=lambda *args, **kwargs: exported.append((args, kwargs))),
    )

    assert executor.try_recover(context, "RecoverState", _FakeAction(), screenshot_path) is True
    assert clicked[0]["label"] == "confirm"
    assert context.extracted["pending_ai_recovery"]["source"] == "memory"
    assert context.extracted["pending_ai_recovery"]["visible_labels"] == ["confirm"]
    assert "Using Memory..." in context.emitted
    assert len(exported) == 1


def test_try_recover_uses_ai_hint_when_memory_has_no_match(monkeypatch, tmp_path):
    memory = _FakeMemory(entry=None)
    executor = AIRecoveryExecutor(memory=memory, detector=SimpleNamespace())
    context = _FakeContext()
    screenshot_path = _screen_path(tmp_path)

    monkeypatch.setattr(executor, "_detections", lambda _path: [SimpleNamespace(label="confirm")])
    monkeypatch.setattr(executor, "_ai_hint", lambda _context, _path: {"label": "confirm", "x": 0.3, "y": 0.4, "confidence": 0.92})
    monkeypatch.setattr(executor, "_click_hint", lambda _context, _hint: True)
    monkeypatch.setattr(
        ai_recovery_executor_module,
        "DetectionDataset",
        lambda: SimpleNamespace(export_stub=lambda *args, **kwargs: None),
    )

    assert executor.try_recover(context, "RecoverState", _FakeAction(), screenshot_path) is True
    assert context.extracted["pending_ai_recovery"]["source"] == "ai"


def test_try_recover_records_failure_when_guarded_click_is_rejected(monkeypatch, tmp_path):
    memory = _FakeMemory(
        {
            "label": "confirm",
            "normalized_point": {"x": 0.25, "y": 0.50},
            "confidence": 0.95,
        }
    )
    executor = AIRecoveryExecutor(memory=memory, detector=SimpleNamespace())
    context = _FakeContext()
    screenshot_path = _screen_path(tmp_path)

    monkeypatch.setattr(executor, "_detections", lambda _path: [SimpleNamespace(label="confirm")])
    monkeypatch.setattr(executor, "_click_hint", lambda _context, _hint: False)
    monkeypatch.setattr(
        ai_recovery_executor_module,
        "DetectionDataset",
        lambda: SimpleNamespace(export_stub=lambda *args, **kwargs: None),
    )

    assert executor.try_recover(context, "RecoverState", _FakeAction(), screenshot_path) is False
    assert len(memory.failure_signatures) == 1
    assert "pending_ai_recovery" not in context.extracted


def test_try_recover_skips_manual_and_captcha_states(tmp_path):
    executor = AIRecoveryExecutor(memory=_FakeMemory(), detector=SimpleNamespace())
    screenshot_path = _screen_path(tmp_path)

    assert executor.try_recover(_FakeContext(), "manual review", _FakeAction(), screenshot_path) is False
    assert executor.try_recover(_FakeContext(), "RecoverState", SimpleNamespace(image="captcha.png"), screenshot_path) is False


def test_verify_pending_records_success_and_clears_pending(monkeypatch):
    memory = _FakeMemory()
    context = _FakeContext()
    context.extracted["pending_ai_recovery"] = {
        "signature": "sig",
        "screenshot_hash": "hash",
        "state_name": "RecoverState",
        "action_class": "FakeAction",
        "action_image": "confirm.png",
        "visible_labels": ["confirm"],
        "label": "confirm",
        "normalized_point": {"x": 0.25, "y": 0.50},
        "confidence": 0.95,
        "source": "ai",
    }
    monkeypatch.setattr(ai_recovery_executor_module.RecoveryMemory, "load", classmethod(lambda cls: memory))

    AIRecoveryExecutor.verify_pending(context, "RecoverState", "NextState", False)

    assert context.emitted == ["Learning..."]
    assert len(memory.success_calls) == 1
    assert "pending_ai_recovery" not in context.extracted


def test_verify_pending_records_failure_when_state_is_still_unknown(monkeypatch):
    memory = _FakeMemory()
    context = _FakeContext()
    context.extracted["pending_ai_recovery"] = {
        "signature": "sig",
        "state_name": "RecoverState",
    }
    fake_module = ModuleType("state_monitor")
    fake_module.GameStateMonitor = lambda _context: SimpleNamespace(is_known_state=lambda: False)
    monkeypatch.setitem(sys.modules, "state_monitor", fake_module)
    monkeypatch.setattr(ai_recovery_executor_module.RecoveryMemory, "load", classmethod(lambda cls: memory))

    AIRecoveryExecutor.verify_pending(context, "RecoverState", "RecoverState", False)

    assert memory.failure_signatures == ["sig"]
    assert "pending_ai_recovery" not in context.extracted


def test_normalize_hint_strips_png_suffix_and_rejects_invalid_values():
    normalized = AIRecoveryExecutor._normalize_hint(
        {"label": "confirm.png", "x": "0.2", "y": "0.3", "confidence": "0.95"}
    )

    assert normalized == {"label": "confirm", "x": 0.2, "y": 0.3, "confidence": 0.95}
    assert AIRecoveryExecutor._normalize_hint({"label": "confirm", "x": "bad"}) is None


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ({"label": "confirm", "x": 0.1, "y": 0.2, "confidence": 0.95}, True),
        ({"label": "unknown", "x": 0.1, "y": 0.2, "confidence": 0.95}, False),
        ({"label": "confirm", "x": 1.5, "y": 0.2, "confidence": 0.95}, False),
        ({"label": "confirm", "x": 0.1, "y": 0.2, "confidence": 0.50}, False),
    ],
)
def test_hint_allowed_enforces_label_confidence_and_bounds(hint, expected):
    assert AIRecoveryExecutor._hint_allowed(hint) is expected


def test_detections_returns_empty_list_when_screenshot_is_missing():
    executor = AIRecoveryExecutor(memory=_FakeMemory(), detector=SimpleNamespace())

    assert executor._detections(None) == []


def test_detections_returns_empty_list_when_detector_raises(monkeypatch, tmp_path):
    screenshot_path = _screen_path(tmp_path)
    executor = AIRecoveryExecutor(memory=_FakeMemory(), detector=SimpleNamespace(detect=lambda _image: (_ for _ in ()).throw(RuntimeError("bad detector"))))

    assert executor._detections(screenshot_path) == []


def test_ai_hint_filters_to_allowed_targets(monkeypatch, tmp_path):
    screenshot_path = _screen_path(tmp_path)
    fake_module = ModuleType("ai_fallback")
    fake_module.AIFallback = lambda: SimpleNamespace(
        analyze_failure=lambda context, path, state_history: {
            "target_hints": [
                {"label": "unknown", "x": 0.1, "y": 0.2, "confidence": 0.99},
                {"label": "confirm", "x": 0.25, "y": 0.75, "confidence": 0.91},
            ]
        }
    )
    monkeypatch.setitem(sys.modules, "ai_fallback", fake_module)
    context = _FakeContext()
    executor = AIRecoveryExecutor(memory=_FakeMemory(), detector=SimpleNamespace())

    hint = executor._ai_hint(context, screenshot_path)

    assert hint == {"label": "confirm", "x": 0.25, "y": 0.75, "confidence": 0.91}
    assert "AI Recovering..." in context.emitted


def test_click_hint_rejects_target_outside_window(monkeypatch):
    fake_window_handler = SimpleNamespace(get_client_window_rect=lambda _title: _WindowRect())
    fake_input_controller = SimpleNamespace(validate_bounds=lambda *args, **kwargs: False)
    monkeypatch.setattr(ai_recovery_executor_module, "WindowHandler", lambda: fake_window_handler)
    monkeypatch.setattr(ai_recovery_executor_module, "InputController", fake_input_controller)

    result = AIRecoveryExecutor(memory=_FakeMemory(), detector=SimpleNamespace())._click_hint(
        _FakeContext(),
        {"label": "confirm", "x": 0.2, "y": 0.3, "confidence": 0.95},
    )

    assert result is False


def test_click_hint_uses_context_runtime_factories():
    calls = []

    class _FactoryContext(_FakeContext):
        def build_window_handler(self):
            return SimpleNamespace(get_client_window_rect=lambda _title: _WindowRect())

        def build_input_controller(self):
            return SimpleNamespace(
                coordinate_noise_px=3,
                click=lambda *args, **kwargs: calls.append(("click", args, kwargs)) or True,
            )

    result = AIRecoveryExecutor(memory=_FakeMemory(), detector=SimpleNamespace())._click_hint(
        _FactoryContext(),
        {"label": "confirm", "x": 0.2, "y": 0.3, "confidence": 0.95},
    )

    assert result is True
    assert calls
