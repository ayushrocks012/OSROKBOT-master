import base64
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from openai import OpenAI
from termcolor import colored

from config_manager import ConfigManager
from vision_memory import VisionMemory


ALLOWED_ACTION_TYPES = {"click", "wait", "stop"}
DEFAULT_MODEL = "gpt-5.4-mini"
MIN_PLANNER_CONFIDENCE = 0.70


@dataclass
class PlannerDecision:
    thought_process: str
    action_type: str
    label: str
    x: float
    y: float
    confidence: float
    reason: str
    source: str = "ai"

    def to_dict(self):
        return asdict(self)


class DynamicPlanner:
    SCHEMA = {
        "type": "object",
        "properties": {
            "thought_process": {"type": "string"},
            "action_type": {"type": "string", "enum": ["click", "wait", "stop"]},
            "label": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["thought_process", "action_type", "label", "x", "y", "confidence", "reason"],
        "additionalProperties": False,
    }

    def __init__(self, config=None, memory=None):
        self.config = config or ConfigManager()
        api_key = self.config.get("OPENAI_KEY") or self.config.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=api_key) if api_key else None
        self.model = self.config.get("OPENAI_VISION_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
        self.memory = memory or VisionMemory()

    @staticmethod
    def _image_data_url(path):
        path = Path(path)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        suffix = path.suffix.lower().lstrip(".") or "png"
        if suffix == "jpg":
            suffix = "jpeg"
        return f"data:image/{suffix};base64,{encoded}"

    @staticmethod
    def _safe_json_loads(text):
        try:
            return json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            raise

    @staticmethod
    def _visible_labels(detections):
        labels = []
        for detection in detections or []:
            if hasattr(detection, "to_dict"):
                detection = detection.to_dict()
            if isinstance(detection, dict):
                labels.append(str(detection.get("label", "")))
        return sorted(label for label in labels if label)

    @staticmethod
    def decision_from_memory(entry):
        point = entry.get("normalized_point", {}) if entry else {}
        return PlannerDecision(
            thought_process="Recovered from local visual memory.",
            action_type=entry.get("action_type", "click"),
            label=entry.get("label", "memory"),
            x=float(point.get("x", 0.0)),
            y=float(point.get("y", 0.0)),
            confidence=float(entry.get("confidence", 0.0)),
            reason=f"Matched prior screen with similarity {entry.get('similarity', 0):.3f}.",
            source="memory",
        )

    @staticmethod
    def validate_decision(decision):
        if not isinstance(decision, PlannerDecision):
            return False
        if decision.action_type not in ALLOWED_ACTION_TYPES:
            return False
        if decision.action_type == "click":
            return (
                decision.confidence >= MIN_PLANNER_CONFIDENCE
                and 0.0 <= decision.x <= 1.0
                and 0.0 <= decision.y <= 1.0
            )
        return True

    def _request_decision(self, context, screenshot_path, detections, ocr_text, goal):
        if not self.client:
            print(colored("Dynamic planner unavailable: OPENAI_KEY/OPENAI_API_KEY is not configured.", "yellow"))
            return None
        if not hasattr(self.client, "responses"):
            print(colored("Dynamic planner unavailable: installed openai package lacks Responses API support.", "yellow"))
            return None

        labels = self._visible_labels(detections)
        history = getattr(context, "state_history", [])[-10:] if context else []
        prompt = (
            "You control a guarded Rise of Kingdoms automation planner. Return one safe next action. "
            "Do not solve captchas. Do not invent UI controls. Use normalized x/y coordinates from 0.0 to 1.0 "
            "for click actions. Prefer wait or stop if the safe next click is unclear.\n\n"
            f"Goal: {goal}\n"
            f"Visible detector labels: {labels}\n"
            f"OCR text: {ocr_text or ''}\n"
            f"Recent history: {json.dumps(history, ensure_ascii=True)}"
        )
        try:
            response = self.client.responses.create(
                model=self.model,
                instructions="Return only the strict JSON object requested by the schema.",
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": self._image_data_url(screenshot_path)},
                        ],
                    }
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "osrokbot_planner_decision",
                        "strict": True,
                        "schema": self.SCHEMA,
                    }
                },
            )
            raw = self._safe_json_loads(response.output_text)
            return PlannerDecision(
                thought_process=str(raw.get("thought_process", "")),
                action_type=str(raw.get("action_type", "wait")).lower(),
                label=str(raw.get("label", "")),
                x=float(raw.get("x", 0.0)),
                y=float(raw.get("y", 0.0)),
                confidence=float(raw.get("confidence", 0.0)),
                reason=str(raw.get("reason", "")),
                source="ai",
            )
        except Exception as exc:
            print(colored(f"Dynamic planner request failed: {exc}", "red"))
            return None

    def plan_next(self, context, screenshot_path, detections, ocr_text, goal):
        labels = self._visible_labels(detections)
        memory_entry = self.memory.find(screenshot_path, labels)
        if memory_entry:
            decision = self.decision_from_memory(memory_entry)
            if self.validate_decision(decision):
                return decision

        decision = self._request_decision(context, screenshot_path, detections, ocr_text, goal)
        if not self.validate_decision(decision):
            print(colored("Dynamic planner rejected an invalid or low-confidence decision.", "yellow"))
            return None
        return decision
