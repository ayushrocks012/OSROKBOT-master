import json
from datetime import datetime
from pathlib import Path

from PIL import Image
from termcolor import colored


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MEMORY_PATH = PROJECT_ROOT / "data" / "recovery_memory.json"


class RecoveryMemory:
    def __init__(self, path=DEFAULT_MEMORY_PATH):
        self.path = Path(path)
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
    def build_signature(cls, state_name, action, screenshot_path, visible_labels=None):
        labels = ",".join(cls.visible_label_values(visible_labels))
        return "|".join(
            [
                str(state_name),
                action.__class__.__name__,
                cls.action_image(action),
                cls.screenshot_hash(screenshot_path),
                labels,
            ]
        )

    def find(self, signature):
        entry = self.entries.get(signature)
        if not entry:
            return None
        if entry.get("failure_count", 0) > entry.get("success_count", 0) + 2:
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
    ):
        now = datetime.now().isoformat(timespec="seconds")
        entry = self.entries.get(signature) or {
            "signature": signature,
            "state_name": state_name,
            "action_image": action_image,
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
        self.entries[signature] = entry
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
