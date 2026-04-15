from pathlib import Path

from PIL import Image
from termcolor import colored

from detection_dataset import DetectionDataset
from input_controller import InputController
from object_detector import create_detector
from recovery_memory import RecoveryMemory
from window_handler import WindowHandler


ALLOWED_LABELS = {
    "confirm",
    "escx",
    "searchaction",
    "gatheraction",
    "attackaction",
    "marchaction",
    "newtroopaction",
    "smallmarchaction",
    "sendaction",
    "useaction",
}
MIN_CONFIDENCE = 0.85
CAPTCHA_IMAGE = "Media/captchachest.png"


class AIRecoveryExecutor:
    """Guarded bridge from advisory AI/memory hints to deterministic input."""

    def __init__(self, memory=None, detector=None):
        self.memory = memory or RecoveryMemory.load()
        self.detector = detector or create_detector()

    @staticmethod
    def _action_image(action):
        return str(getattr(action, "image", "") or "")

    @staticmethod
    def _is_manual_or_captcha(state_name, action):
        action_image = AIRecoveryExecutor._action_image(action).replace("\\", "/")
        state_text = str(state_name).lower()
        return (
            action_image == CAPTCHA_IMAGE
            or action_image.endswith("/captchachest.png")
            or "captcha" in state_text
            or "manual" in state_text
        )

    @staticmethod
    def _normalize_hint(raw_hint):
        if not raw_hint:
            return None
        try:
            label = str(raw_hint.get("label", "")).lower().strip()
            x = float(raw_hint.get("x"))
            y = float(raw_hint.get("y"))
            confidence = float(raw_hint.get("confidence", 0.0))
        except Exception:
            return None

        if label.endswith(".png"):
            label = Path(label).stem
        return {
            "label": label,
            "x": x,
            "y": y,
            "confidence": confidence,
        }

    @staticmethod
    def _hint_allowed(hint):
        if not hint:
            return False
        return (
            hint["label"] in ALLOWED_LABELS
            and hint["confidence"] >= MIN_CONFIDENCE
            and 0.0 <= hint["x"] <= 1.0
            and 0.0 <= hint["y"] <= 1.0
        )

    def _detections(self, screenshot_path):
        if not screenshot_path:
            return []
        try:
            screenshot = Image.open(screenshot_path).convert("RGB")
            return self.detector.detect(screenshot)
        except Exception as exc:
            print(colored(f"Object detection skipped: {exc}", "yellow"))
            return []

    def _memory_hint(self, signature_parts):
        entry = self.memory.find(signature_parts)
        if not entry:
            return None
        if hasattr(self, "_emit") and self._emit:
            self._emit("Using Memory...")
        point = entry.get("normalized_point")
        if not isinstance(point, dict):
            return None
        return self._normalize_hint(
            {
                "label": entry.get("label"),
                "x": point.get("x"),
                "y": point.get("y"),
                "confidence": entry.get("confidence", 0.0),
            }
        )

    def _ai_hint(self, context, screenshot_path):
        try:
            from ai_fallback import AIFallback
        except Exception as exc:
            print(colored(f"AI recovery unavailable: {exc}", "yellow"))
            return None

        if context:
            context.emit_state("AI Recovering...")
        result = AIFallback().analyze_failure(
            context,
            screenshot_path,
            getattr(context, "state_history", []),
        )
        if not result:
            return None

        hints = [
            self._normalize_hint(hint)
            for hint in result.get("target_hints", [])
        ]
        allowed_hints = [hint for hint in hints if self._hint_allowed(hint)]
        if not allowed_hints:
            print(colored("AI recovery produced no allowed target hints.", "yellow"))
            return None
        return max(allowed_hints, key=lambda hint: hint["confidence"])

    def _click_hint(self, context, hint):
        window_rect = WindowHandler().get_client_window_rect(context.window_title)
        if not window_rect:
            return False

        target_x, target_y = context.resolve_anchor_relative_point(hint["x"], hint["y"], window_rect)
        if not InputController.validate_bounds(target_x, target_y, window_rect):
            print(colored(f"AI recovery target rejected outside window: {target_x}, {target_y}", "red"))
            return False

        print(colored(f"AI recovery guarded click: {hint['label']} ({hint['x']:.3f}, {hint['y']:.3f})", "cyan"))
        return InputController(context=context, coordinate_noise_px=0).click(
            target_x,
            target_y,
            window_rect=window_rect,
            remember_position=False,
            context=context,
        )

    def try_recover(self, context, state_name, action, screenshot_path):
        if not context or not screenshot_path:
            return False
        if self._is_manual_or_captcha(state_name, action):
            print(colored("AI recovery skipped for captcha/manual state.", "yellow"))
            return False

        detections = self._detections(screenshot_path)
        visible_labels = RecoveryMemory.visible_label_values(detections)
        DetectionDataset().export_stub(
            screenshot_path,
            state_name,
            action_image=self._action_image(action),
            detections=detections,
        )
        signature_parts = RecoveryMemory.signature_parts(state_name, action, screenshot_path, detections)
        signature = RecoveryMemory.stable_signature(signature_parts)

        self._emit = context.emit_state if context else None
        hint = self._memory_hint(signature_parts)
        source = "memory"
        if not hint:
            hint = self._ai_hint(context, screenshot_path)
            source = "ai"

        if not self._hint_allowed(hint):
            return False
        if not self._click_hint(context, hint):
            self.memory.record_failure(signature)
            return False

        context.extracted["pending_ai_recovery"] = {
            "signature": signature,
            "screenshot_hash": signature_parts["screenshot_hash"],
            "state_name": state_name,
            "action_class": action.__class__.__name__,
            "action_image": self._action_image(action),
            "visible_labels": visible_labels,
            "label": hint["label"],
            "normalized_point": {"x": hint["x"], "y": hint["y"]},
            "confidence": hint["confidence"],
            "source": source,
        }
        return True

    @staticmethod
    def verify_pending(context, previous_state, next_state, result):
        pending = getattr(context, "extracted", {}).get("pending_ai_recovery") if context else None
        if not pending:
            return

        recovered = bool(result) or next_state != pending.get("state_name")
        if not recovered:
            try:
                from state_monitor import GameStateMonitor

                recovered = GameStateMonitor(context).is_known_state()
            except Exception:
                recovered = False

        memory = RecoveryMemory.load()
        if recovered:
            context.emit_state("Learning...")
            memory.record_success(
                pending["signature"],
                pending["state_name"],
                pending["action_image"],
                pending["label"],
                pending["normalized_point"],
                pending["confidence"],
                source=pending.get("source", "ai"),
                screenshot_hash=pending.get("screenshot_hash"),
                action_class=pending.get("action_class", ""),
                visible_labels=pending.get("visible_labels", []),
            )
        else:
            memory.record_failure(pending["signature"])

        context.extracted.pop("pending_ai_recovery", None)
