import json
from datetime import datetime
from pathlib import Path

from PIL import Image
from termcolor import colored


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MEMORY_PATH = PROJECT_ROOT / "data" / "recovery_memory.json"


class RecoveryMemory:
    def __init__(self, path=DEFAULT_MEMORY_PATH, hash_tolerance=4):
        self.path = Path(path)
        self.hash_tolerance = int(hash_tolerance)
        self.entries = {}

    @classmethod
    def load(cls, path=DEFAULT_MEMORY_PATH):
        memory = cls(path)
        if not memory.path.is_file():
            return memory

        try:
            raw = json.loads(memory.path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(colored(f"Recovery memory ignored: {exc}", "yellow"))
            return memory

        entries = raw.get("entries", raw if isinstance(raw, list) else [])
        for entry in entries:
            signature = entry.get("signature")
            if signature:
                memory.entries[signature] = entry
        return memory

    @staticmethod
    def screenshot_hash(screenshot_path):
        try:
            image = Image.open(screenshot_path).convert("L").resize((8, 8))
        except Exception:
            return "no_screenshot"

        pixels = list(image.getdata())
        average = sum(pixels) / max(1, len(pixels))
        bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
        return f"{int(bits, 2):016x}"

    @staticmethod
    def action_image(action):
        return str(getattr(action, "image", "") or "")

    @staticmethod
    def visible_label_values(visible_labels):
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
        return cls.stable_signature(cls.signature_parts(state_name, action, screenshot_path, visible_labels))

    @staticmethod
    def hamming_distance(left_hash, right_hash):
        if not left_hash or not right_hash or left_hash == "no_screenshot" or right_hash == "no_screenshot":
            return 999
        try:
            return (int(left_hash, 16) ^ int(right_hash, 16)).bit_count()
        except Exception:
            return 999

    def _compatible_entries(self, stable_signature, screenshot_hash):
        for entry in self.entries.values():
            if entry.get("signature") != stable_signature:
                continue
            if entry.get("failure_count", 0) > entry.get("success_count", 0) + 2:
                continue
            distance = self.hamming_distance(screenshot_hash, entry.get("screenshot_hash"))
            if distance <= self.hash_tolerance:
                yield distance, entry

    def find(self, signature, screenshot_hash=None):
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

        entry = self.entries.get(stable_signature)
        if not entry:
            return None
        if entry.get("failure_count", 0) > entry.get("success_count", 0) + 2:
            return None
        if screenshot_hash and self.hamming_distance(screenshot_hash, entry.get("screenshot_hash")) > self.hash_tolerance:
            return None
        return entry

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "entries": sorted(self.entries.values(), key=lambda entry: entry.get("last_used", "")),
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

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
        now = datetime.now().isoformat(timespec="seconds")
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
        print(colored(f"Recovery memory success recorded: {label}", "cyan"))
        return entry

    def record_failure(self, signature):
        now = datetime.now().isoformat(timespec="seconds")
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
        print(colored("Recovery memory failure recorded.", "yellow"))
        return entry
