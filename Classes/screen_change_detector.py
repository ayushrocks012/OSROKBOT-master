"""Screen change detection and loop/stuck-state monitoring.

Tracks perceptual hashes of consecutive screenshots and detects:
- Stuck screens (< threshold change over N cycles)
- Repeated actions (same target_id + action_type N times)
"""

from collections import deque
from dataclasses import dataclass, field

import numpy as np
from config_manager import ConfigManager
from logging_config import get_logger
from PIL import Image

LOGGER = get_logger(__name__)


DEFAULT_STUCK_THRESHOLD = 3
DEFAULT_REPEAT_THRESHOLD = 4
HASH_SIZE = 8


@dataclass
class ScreenChangeDetector:
    """Detects stuck screens and repeated actions in the planner loop."""

    stuck_threshold: int = DEFAULT_STUCK_THRESHOLD
    repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD
    _hash_history: deque = field(default_factory=lambda: deque(maxlen=20))
    _action_history: deque = field(default_factory=lambda: deque(maxlen=40))
    _last_hash: str = ""
    config: ConfigManager = field(default_factory=ConfigManager)

    @staticmethod
    def perceptual_hash(image_or_path):
        """Compute a perceptual hash for an image.

        Args:
            image_or_path: PIL Image or path to an image file.

        Returns:
            str: Hex string perceptual hash.
        """
        try:
            if not isinstance(image_or_path, Image.Image):
                image_or_path = Image.open(image_or_path)
            image = image_or_path.convert("L").resize(
                (HASH_SIZE, HASH_SIZE), Image.Resampling.LANCZOS
            )
        except Exception:
            return ""

        pixels = list(image.getdata())
        average = sum(pixels) / max(1, len(pixels))
        bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
        return f"{int(bits, 2):016x}"

    @staticmethod
    def hamming_distance(hash_a, hash_b):
        """Hamming distance between two hex hash strings."""
        if not hash_a or not hash_b:
            return 999
        try:
            return (int(hash_a, 16) ^ int(hash_b, 16)).bit_count()
        except Exception:
            return 999

    @staticmethod
    def screen_similarity(image_a, image_b, size=(64, 64)):
        """Compute a simple structural similarity between two images.

        Returns a float from 0.0 (completely different) to 1.0 (identical).
        Uses normalized cross-correlation on grayscale thumbnails.
        """
        try:
            if not isinstance(image_a, Image.Image):
                image_a = Image.open(image_a)
            if not isinstance(image_b, Image.Image):
                image_b = Image.open(image_b)

            arr_a = np.asarray(
                image_a.convert("L").resize(size, Image.Resampling.LANCZOS),
                dtype="float32",
            ).flatten()
            arr_b = np.asarray(
                image_b.convert("L").resize(size, Image.Resampling.LANCZOS),
                dtype="float32",
            ).flatten()

            norm_a = np.linalg.norm(arr_a)
            norm_b = np.linalg.norm(arr_b)
            if norm_a <= 0 or norm_b <= 0:
                return 0.0
            return float(np.dot(arr_a, arr_b) / (norm_a * norm_b))
        except Exception:
            return 0.0

    def record_screenshot(self, image_or_path):
        """Record a perceptual hash for the latest screenshot.

        Args:
            image_or_path: PIL Image or path to screenshot.

        Returns:
            str: The computed perceptual hash.
        """
        current_hash = self.perceptual_hash(image_or_path)
        self._hash_history.append(current_hash)
        self._last_hash = current_hash
        return current_hash

    def record_action(self, action_type, target_id="", label=""):
        """Record an action for repetition tracking.

        Args:
            action_type: The action type (click, wait, stop, etc.).
            target_id: The target ID that was acted on.
            label: Human-readable label.
        """
        self._action_history.append({
            "action_type": str(action_type),
            "target_id": str(target_id),
            "label": str(label),
        })

    def is_screen_stuck(self):
        """Check if the screen hasn't changed for stuck_threshold consecutive cycles.

        Returns:
            bool: True if the last N screenshots are nearly identical.
        """
        if len(self._hash_history) < self.stuck_threshold:
            return False

        recent = list(self._hash_history)[-self.stuck_threshold:]
        reference = recent[-1]
        threshold = int(self.config.get("STUCK_HAMMING_THRESHOLD", 3))
        return all(self.hamming_distance(reference, past_hash) <= threshold for past_hash in recent[:-1])

    def screen_changed_since_last(self):
        """Check if the current screenshot differs from the previous one.

        Returns:
            bool: True if there was a meaningful screen change, or if
            there is insufficient history to compare.
        """
        if len(self._hash_history) < 2:
            return True
        hashes = list(self._hash_history)
        threshold = int(self.config.get("STUCK_HAMMING_THRESHOLD", 3))
        return self.hamming_distance(hashes[-1], hashes[-2]) > threshold

    def repeated_action_count(self, action_type=None, target_id=None):
        """Count consecutive trailing repetitions of the same action.

        Args:
            action_type: If given, only count this action type.
            target_id: If given, only count this target ID.

        Returns:
            int: Number of consecutive trailing identical actions.
        """
        if not self._action_history:
            return 0

        last = self._action_history[-1]
        check_type = action_type or last["action_type"]
        check_target = target_id or last["target_id"]
        count = 0

        for action in reversed(self._action_history):
            if action["action_type"] == check_type and action["target_id"] == check_target:
                count += 1
            else:
                break
        return count

    def is_action_repeating(self, action_type=None, target_id=None):
        """Check if the same action has been repeated above the threshold.

        Returns:
            bool: True if the action is repeating excessively.
        """
        return self.repeated_action_count(action_type, target_id) >= self.repeat_threshold

    def stuck_warning_text(self):
        """Generate a warning message for the planner prompt when stuck.

        Returns:
            str: Warning text, or empty string if not stuck.
        """
        parts = []
        if self.is_screen_stuck():
            parts.append(
                f"WARNING: The screen has not changed for {self.stuck_threshold} "
                f"consecutive cycles. You may be stuck. Try a different action, "
                f"press Escape to close a dialog, or use 'wait' to let an "
                f"animation complete."
            )

        if self._action_history:
            last = self._action_history[-1]
            repeat_count = self.repeated_action_count()
            if repeat_count >= self.repeat_threshold:
                parts.append(
                    f"WARNING: You have performed '{last['action_type']}' on "
                    f"target '{last['label']}' ({last['target_id']}) "
                    f"{repeat_count} times in a row with no visible effect. "
                    f"Choose a different target or action."
                )

        return "\n".join(parts)

    def reset(self):
        """Clear all tracking history."""
        self._hash_history.clear()
        self._action_history.clear()
        self._last_hash = ""
