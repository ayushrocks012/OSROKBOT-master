"""Bounded persistence for guarded recovery outcomes.

This module stores successful and failed recovery attempts keyed by stable
state/action signatures plus a coarse screenshot hash. The runtime uses it as
an advisory memory layer for repeated failure states; it is not allowed to
override the planner or input safety model.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from logging_config import get_logger
from PIL import Image

LOGGER = get_logger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MEMORY_PATH = PROJECT_ROOT / "data" / "recovery_memory.json"
DEFAULT_MAX_ENTRIES = 500


class RecoveryMemory:
    """Store bounded recovery outcomes keyed by stable workflow signatures."""

    def __init__(self, path=DEFAULT_MEMORY_PATH, hash_tolerance=4, max_entries=DEFAULT_MAX_ENTRIES):
        self.path = Path(path)
        self.hash_tolerance = int(hash_tolerance)
        self.max_entries = int(max_entries)
        self.entries = {}
        self._lock = threading.RLock()

    @classmethod
    def load(cls, path=DEFAULT_MEMORY_PATH, max_entries=DEFAULT_MAX_ENTRIES):
        """Load recovery memory from disk, ignoring unreadable payloads."""

        memory = cls(path, max_entries=max_entries)
        if not memory.path.is_file():
            return memory

        try:
            raw = json.loads(memory.path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning(f"Recovery memory ignored: {exc}")
            return memory

        entries = raw.get("entries", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        with memory._lock:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                signature = entry.get("signature")
                if signature:
                    memory.entries[signature] = entry
            memory._evict_if_needed()
        return memory

    @staticmethod
    def screenshot_hash(screenshot_path):
        """Return a coarse perceptual hash for one screenshot path."""

        try:
            image = Image.open(screenshot_path).convert("L").resize((8, 8))
        except Exception:
            return "no_screenshot"

        pixels = list(image.get_flattened_data()) if hasattr(image, "get_flattened_data") else list(image.getdata())
        average = sum(pixels) / max(1, len(pixels))
        bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
        return f"{int(bits, 2):016x}"

    @staticmethod
    def action_image(action):
        """Return the legacy action image identifier for one action object."""

        return str(getattr(action, "image", "") or "")

    @staticmethod
    def visible_label_values(visible_labels):
        """Normalize visible detector labels into a sorted list of strings."""

        labels = []
        for item in visible_labels or []:
            if isinstance(item, str):
                labels.append(item)
            elif isinstance(item, dict):
                labels.append(str(item.get("label", "")))
            elif hasattr(item, "label"):
                labels.append(str(item.label))
        return sorted(label for label in labels if label)

    @classmethod
    def signature_parts(cls, state_name, action, screenshot_path, visible_labels=None):
        """Build the structured signature parts for one recovery scenario."""

        labels = cls.visible_label_values(visible_labels)
        return {
            "state_name": str(state_name),
            "action_class": action.__class__.__name__,
            "action_image": cls.action_image(action),
            "screenshot_hash": cls.screenshot_hash(screenshot_path),
            "visible_labels": labels,
        }

    @staticmethod
    def stable_signature(parts):
        """Return the stable non-hash signature string for memory lookup."""

        labels = ",".join(parts.get("visible_labels", []))
        return "|".join(
            [
                parts.get("state_name", ""),
                parts.get("action_class", ""),
                parts.get("action_image", ""),
                labels,
            ]
        )

    @classmethod
    def build_signature(cls, state_name, action, screenshot_path, visible_labels=None):
        """Build the stable signature string for one recovery scenario."""

        return cls.stable_signature(cls.signature_parts(state_name, action, screenshot_path, visible_labels))

    @staticmethod
    def hamming_distance(left_hash, right_hash):
        """Return the Hamming distance between two coarse screenshot hashes."""

        if not left_hash or not right_hash or left_hash == "no_screenshot" or right_hash == "no_screenshot":
            return 999
        try:
            return (int(left_hash, 16) ^ int(right_hash, 16)).bit_count()
        except Exception:
            return 999

    def _compatible_entries(self, stable_signature, screenshot_hash):
        with self._lock:
            entries = list(self.entries.values())
        for entry in entries:
            if entry.get("signature") != stable_signature:
                continue
            if entry.get("failure_count", 0) > entry.get("success_count", 0) + 2:
                continue
            distance = self.hamming_distance(screenshot_hash, entry.get("screenshot_hash"))
            if distance <= self.hash_tolerance:
                yield distance, entry

    def find(self, signature, screenshot_hash=None):
        """Return the best compatible recovery-memory entry, if any."""

        if screenshot_hash is None and isinstance(signature, dict):
            parts = signature
            stable_signature = self.stable_signature(parts)
            screenshot_hash = parts.get("screenshot_hash")
        elif screenshot_hash is None:
            parts = str(signature).split("|")
            if len(parts) >= 5:
                stable_signature = "|".join([parts[0], parts[1], parts[2], parts[4]])
                screenshot_hash = parts[3]
            else:
                stable_signature = str(signature)
        else:
            stable_signature = str(signature)

        candidates = sorted(
            self._compatible_entries(stable_signature, screenshot_hash),
            key=lambda item: (item[0], -int(item[1].get("success_count", 0))),
        )
        if candidates:
            return candidates[0][1]

        with self._lock:
            entry = self.entries.get(stable_signature)
        if not entry:
            return None
        if entry.get("failure_count", 0) > entry.get("success_count", 0) + 2:
            return None
        if screenshot_hash and self.hamming_distance(screenshot_hash, entry.get("screenshot_hash")) > self.hash_tolerance:
            return None
        return entry

    def save(self):
        """Persist the bounded recovery-memory payload atomically."""

        with self._lock:
            self._evict_if_needed()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 2,
                "entries": sorted(self.entries.values(), key=lambda entry: entry.get("last_used", "")),
            }
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            import time
            for attempt in range(4):
                try:
                    temp_path.replace(self.path)
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    time.sleep(0.1)

    def _evict_if_needed(self):
        if len(self.entries) <= self.max_entries:
            return
        scored = sorted(
            self.entries.values(),
            key=lambda entry: (
                int(entry.get("success_count", 0)) - int(entry.get("failure_count", 0)),
                entry.get("last_used", ""),
            ),
            reverse=True,
        )
        retained = scored[: self.max_entries]
        self.entries = {entry["signature"]: entry for entry in retained if entry.get("signature")}

    def record_success(
        self,
        signature,
        state_name,
        action_image,
        label,
        normalized_point,
        confidence,
        source="ai",
        screenshot_hash=None,
        action_class="",
        visible_labels=None,
    ):
        """Record one successful recovery outcome and persist the memory store."""

        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            stable_signature = signature
            entry = self.entries.get(signature) or {
                "signature": stable_signature,
                "state_name": state_name,
                "action_class": action_class,
                "action_image": action_image,
                "screenshot_hash": screenshot_hash or "",
                "visible_labels": visible_labels or [],
                "label": label,
                "normalized_point": normalized_point,
                "confidence": float(confidence),
                "success_count": 0,
                "failure_count": 0,
                "source": source,
            }
            entry["success_count"] = int(entry.get("success_count", 0)) + 1
            entry["last_used"] = now
            entry["label"] = label
            entry["normalized_point"] = normalized_point
            entry["confidence"] = float(confidence)
            entry["source"] = source
            if screenshot_hash:
                entry["screenshot_hash"] = screenshot_hash
            if action_class:
                entry["action_class"] = action_class
            if visible_labels is not None:
                entry["visible_labels"] = visible_labels
            self.entries[stable_signature] = entry
            self.save()
        LOGGER.info(f"Recovery memory success recorded: {label}")
        return entry

    def record_failure(self, signature):
        """Record one failed recovery attempt and persist the memory store."""

        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            entry = self.entries.get(signature) or {
                "signature": signature,
                "state_name": "",
                "action_image": "",
                "label": "",
                "normalized_point": None,
                "confidence": 0.0,
                "success_count": 0,
                "failure_count": 0,
                "source": "unknown",
            }
            entry["failure_count"] = int(entry.get("failure_count", 0)) + 1
            entry["last_used"] = now
            self.entries[signature] = entry
            self.save()
        LOGGER.warning("Recovery memory failure recorded.")
        return entry
