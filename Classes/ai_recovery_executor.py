from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast

from detection_dataset import DetectionDataset
from input_controller import InputController
from logging_config import get_logger
from object_detector import create_detector
from PIL import Image
from recovery_memory import RecoveryMemory
from runtime_payloads import NormalizedPoint, PendingRecoveryPayload
from window_handler import WindowHandler

LOGGER = get_logger(__name__)


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


class RecoveryHint(TypedDict):
    """One normalized recovery target candidate."""

    label: str
    x: float
    y: float
    confidence: float


class AIRecoveryExecutor:
    """Guarded bridge from advisory AI/memory hints to deterministic input."""

    def __init__(self, memory: Any | None = None, detector: Any | None = None) -> None:
        self.memory = memory or RecoveryMemory.load()
        self.detector = detector or create_detector()
        self._emit: Callable[[str], None] | None = None

    @staticmethod
    def _action_image(action: object) -> str:
        return str(getattr(action, "image", "") or "")

    @staticmethod
    def _is_manual_or_captcha(state_name: object, action: object) -> bool:
        action_image = AIRecoveryExecutor._action_image(action).replace("\\", "/")
        state_text = str(state_name).lower()
        return (
            "captcha" in action_image.lower()
            or "captcha" in state_text
            or "manual" in state_text
        )

    @staticmethod
    def _normalize_hint(raw_hint: object) -> RecoveryHint | None:
        if not raw_hint:
            return None
        if not isinstance(raw_hint, dict):
            return None
        x_value = raw_hint.get("x")
        y_value = raw_hint.get("y")
        if x_value is None or y_value is None:
            return None
        try:
            label = str(raw_hint.get("label", "")).lower().strip()
            x = float(x_value)
            y = float(y_value)
            confidence = float(raw_hint.get("confidence", 0.0))
        except (TypeError, ValueError):
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
    def _hint_allowed(hint: RecoveryHint | None) -> bool:
        if not hint:
            return False
        return (
            hint["label"] in ALLOWED_LABELS
            and hint["confidence"] >= MIN_CONFIDENCE
            and 0.0 <= hint["x"] <= 1.0
            and 0.0 <= hint["y"] <= 1.0
        )

    def _detections(self, screenshot_path: str | Path | None) -> list[Any]:
        if not screenshot_path:
            return []
        try:
            screenshot = Image.open(screenshot_path).convert("RGB")
            return list(self.detector.detect(screenshot))
        except Exception as exc:
            LOGGER.warning(f"Object detection skipped: {exc}")
            return []

    def _memory_hint(self, signature_parts: dict[str, object]) -> RecoveryHint | None:
        entry = self.memory.find(signature_parts)
        if not isinstance(entry, dict):
            return None
        if self._emit:
            self._emit("Using Memory...")
        point = entry.get("normalized_point")
        if not isinstance(point, dict):
            return None
        point_x = point.get("x")
        point_y = point.get("y")
        if point_x is None or point_y is None:
            return None
        return self._normalize_hint(
            {
                "label": entry.get("label"),
                "x": point_x,
                "y": point_y,
                "confidence": entry.get("confidence", 0.0),
            }
        )

    def _ai_hint(self, context: Any | None, screenshot_path: str | Path) -> RecoveryHint | None:
        try:
            from ai_fallback import AIFallback
        except Exception as exc:
            LOGGER.warning(f"AI recovery unavailable: {exc}")
            return None

        if context:
            context.emit_state("AI Recovering...")
        result = AIFallback().analyze_failure(
            context,
            screenshot_path,
            getattr(context, "state_history", []),
        )
        if not isinstance(result, dict):
            return None

        raw_hints = result.get("target_hints", [])
        if not isinstance(raw_hints, list):
            return None
        hints: list[RecoveryHint] = []
        for hint in raw_hints:
            normalized_hint = self._normalize_hint(hint)
            if normalized_hint is not None:
                hints.append(normalized_hint)
        allowed_hints = [hint for hint in hints if self._hint_allowed(hint)]
        if not allowed_hints:
            LOGGER.warning("AI recovery produced no allowed target hints.")
            return None
        return max(allowed_hints, key=lambda hint: hint["confidence"])

    def _click_hint(self, context: Any, hint: RecoveryHint) -> bool:
        window_rect = WindowHandler().get_client_window_rect(context.window_title)
        if not window_rect:
            return False

        target_x, target_y = context.resolve_anchor_relative_point(hint["x"], hint["y"], window_rect)
        if not InputController.validate_bounds(target_x, target_y, window_rect):
            LOGGER.error(f"AI recovery target rejected outside window: {target_x}, {target_y}")
            return False

        LOGGER.info(f"AI recovery guarded click: {hint['label']} ({hint['x']:.3f}, {hint['y']:.3f})")
        return bool(
            InputController(context=context, coordinate_noise_px=0).click(
                target_x,
                target_y,
                window_rect=window_rect,
                remember_position=False,
                context=context,
            )
        )

    def try_recover(
        self,
        context: Any | None,
        state_name: str,
        action: object,
        screenshot_path: str | Path | None,
    ) -> bool:
        if not context or not screenshot_path:
            return False
        if self._is_manual_or_captcha(state_name, action):
            LOGGER.warning("AI recovery skipped for captcha/manual state.")
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

        if hint is None or not self._hint_allowed(hint):
            return False
        if not self._click_hint(context, hint):
            self.memory.record_failure(signature)
            return False

        normalized_point: NormalizedPoint = {"x": hint["x"], "y": hint["y"]}
        pending: PendingRecoveryPayload = {
            "signature": signature,
            "screenshot_hash": str(signature_parts.get("screenshot_hash", "")),
            "state_name": state_name,
            "action_class": action.__class__.__name__,
            "action_image": self._action_image(action),
            "visible_labels": visible_labels,
            "label": hint["label"],
            "normalized_point": normalized_point,
            "confidence": hint["confidence"],
            "source": source,
        }
        context.extracted["pending_ai_recovery"] = pending
        return True

    @staticmethod
    def verify_pending(
        context: Any | None,
        previous_state: str,
        next_state: str | None,
        result: bool,
    ) -> None:
        del previous_state
        if context is None:
            return
        pending_raw = getattr(context, "extracted", {}).get("pending_ai_recovery")
        if not pending_raw:
            return
        if not isinstance(pending_raw, dict):
            return
        pending = cast(PendingRecoveryPayload, pending_raw)

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
