import base64
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from config_manager import ConfigManager
from logging_config import get_logger
from openai import OpenAI
from vision_memory import VisionMemory

LOGGER = get_logger(__name__)


ALLOWED_ACTION_TYPES = {"click", "wait", "stop"}
DEFAULT_MODEL = "gpt-5.4-mini"
MIN_PLANNER_CONFIDENCE = 0.70


@dataclass
class PlannerDecision:
    """One structured action proposed by the AI planner.

    Attributes:
        thought_process: Short model explanation shown only for debugging.
        action_type: One of `click`, `wait`, or `stop`.
        label: Human-readable target name, such as `gather button`.
        x: Normalized horizontal coordinate from 0.0 to 1.0.
        y: Normalized vertical coordinate from 0.0 to 1.0.
        confidence: Model confidence from 0.0 to 1.0.
        reason: Short user-facing reason for the action.
        source: Where the decision came from, usually `ai` or `memory`.
    """

    thought_process: str
    action_type: str
    label: str
    x: float
    y: float
    confidence: float
    reason: str
    source: str = "ai"

    @classmethod
    def from_mapping(cls, raw, source="ai"):
        """Create a planner decision from model or memory JSON.

        Args:
            raw: Mapping-like object containing the planner JSON fields.
            source: Source tag stored on the decision.

        Returns:
            PlannerDecision: A normalized decision object. Invalid numeric
            fields are converted by Python and will raise `ValueError`.
        """
        return cls(
            thought_process=str(raw.get("thought_process", "")),
            action_type=str(raw.get("action_type", "wait")).lower(),
            label=str(raw.get("label", "")),
            x=float(raw.get("x", 0.0)),
            y=float(raw.get("y", 0.0)),
            confidence=float(raw.get("confidence", 0.0)),
            reason=str(raw.get("reason", "")),
            source=source,
        )

    def to_dict(self):
        """Return a JSON-serializable dictionary for Context and UI payloads."""
        return asdict(self)


class DynamicPlanner:
    """Vision-language planner for one guarded OSROKBOT step.

    `DynamicPlanner` is intentionally side-effect free. It never moves the
    mouse and never changes game state. It reads the current screenshot,
    optional detector labels, OCR text, and mission goal, then returns a
    validated `PlannerDecision`. `DynamicPlannerAction` is responsible for
    HITL approval, bounds checks, memory writes, and Interception input.
    """

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
        """Initialize the planner using ConfigManager and optional memory.

        Args:
            config: Optional ConfigManager-like object for API keys/model name.
            memory: Optional VisionMemory instance used before OpenAI calls.
        """
        self.config = config or ConfigManager()
        api_key = self.config.get("OPENAI_KEY") or self.config.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=api_key) if api_key else None
        self.model = self.config.get("OPENAI_VISION_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
        self.memory = memory or VisionMemory()

    @staticmethod
    def _image_data_url(path):
        """Encode an image file as a data URL for OpenAI vision input.

        Args:
            path: Path to a PNG/JPEG screenshot.

        Returns:
            str: A `data:image/...;base64,...` URL.
        """
        path = Path(path)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        suffix = path.suffix.lower().lstrip(".") or "png"
        if suffix == "jpg":
            suffix = "jpeg"
        return f"data:image/{suffix};base64,{encoded}"

    @staticmethod
    def _safe_json_loads(text):
        """Parse strict JSON, with a small fallback for wrapped model output.

        Args:
            text: Raw model output text.

        Returns:
            dict: Parsed JSON object.
        """
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
        """Normalize detector outputs into a sorted list of visible labels.

        Args:
            detections: Detection objects or dictionaries.

        Returns:
            list[str]: Non-empty labels from the detector output.
        """
        labels = []
        for detection in detections or []:
            if hasattr(detection, "to_dict"):
                detection = detection.to_dict()
            if isinstance(detection, dict):
                labels.append(str(detection.get("label", "")))
        return sorted(label for label in labels if label)

    @staticmethod
    def decision_from_memory(entry):
        """Convert a VisionMemory entry into a planner decision.

        Args:
            entry: Memory entry returned by `VisionMemory.find`.

        Returns:
            PlannerDecision: A memory-sourced click/wait/stop decision.
        """
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
        """Validate that a decision is safe enough to enter HITL/input flow.

        Args:
            decision: Candidate PlannerDecision.

        Returns:
            bool: True when the action type is supported and click coordinates
            are finite, normalized, and above the minimum confidence threshold.
        """
        if not isinstance(decision, PlannerDecision):
            return False
        if decision.action_type not in ALLOWED_ACTION_TYPES:
            return False
        if not math.isfinite(decision.confidence):
            return False
        if decision.action_type == "click":
            return (
                math.isfinite(decision.x)
                and math.isfinite(decision.y)
                and decision.confidence >= MIN_PLANNER_CONFIDENCE
                and 0.0 <= decision.x <= 1.0
                and 0.0 <= decision.y <= 1.0
            )
        return True

    def _request_decision(self, context, screenshot_path, detections, ocr_text, goal):
        """Ask OpenAI for a single next action in strict JSON format.

        Args:
            context: Current OSROKBOT runtime context.
            screenshot_path: Screenshot file to send as vision input.
            detections: YOLO detections visible on screen.
            ocr_text: OCR text extracted from the screenshot.
            goal: Natural-language mission from the Commander UI.

        Returns:
            PlannerDecision | None: Parsed model decision, or None if the
            request fails.
        """
        if not self.client:
            LOGGER.warning("Dynamic planner unavailable: OPENAI_KEY/OPENAI_API_KEY is not configured.")
            return None
        if not hasattr(self.client, "responses"):
            LOGGER.warning("Dynamic planner unavailable: installed openai package lacks Responses API support.")
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
            return PlannerDecision.from_mapping(raw, source="ai")
        except Exception as exc:
            LOGGER.error(f"Dynamic planner request failed: {exc}")
            return None

    def plan_next(self, context, screenshot_path, detections, ocr_text, goal):
        """Return the next safe planner decision for the current screen.

        Args:
            context: Current OSROKBOT runtime context.
            screenshot_path: Current screenshot path.
            detections: Object detector outputs for the screenshot.
            ocr_text: OCR text from the screenshot.
            goal: Natural-language mission prompt.

        Returns:
            PlannerDecision | None: A validated memory or AI decision. None is
            returned when no safe decision is available.
        """
        labels = self._visible_labels(detections)
        memory_entry = self.memory.find(screenshot_path, labels)
        if memory_entry:
            decision = self.decision_from_memory(memory_entry)
            if self.validate_decision(decision):
                return decision

        decision = self._request_decision(context, screenshot_path, detections, ocr_text, goal)
        if not self.validate_decision(decision):
            LOGGER.warning("Dynamic planner rejected an invalid or low-confidence decision.")
            return None
        return decision
