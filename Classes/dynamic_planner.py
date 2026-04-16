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
MAX_PLANNER_DELAY_SECONDS = 10.0


def _safe_float(value, default=math.nan):
    try:
        return float(value)
    except Exception:
        return default


def _clamp_delay(value):
    delay = _safe_float(value, 1.0)
    if not math.isfinite(delay):
        return 1.0
    return max(0.0, min(MAX_PLANNER_DELAY_SECONDS, delay))


@dataclass(frozen=True)
class PlannerTarget:
    """A locally observed click target that the model may reference by ID."""

    target_id: str
    label: str
    x: float
    y: float
    width: float
    height: float
    confidence: float
    source: str

    def to_prompt_dict(self):
        return {
            "id": self.target_id,
            "source": self.source,
            "label": self.label,
            "center": {"x": round(self.x, 4), "y": round(self.y, 4)},
            "size": {"width": round(self.width, 4), "height": round(self.height, 4)},
            "confidence": round(self.confidence, 4),
        }


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
        target_id: Local detector/OCR target ID used to resolve click coordinates.
        delay_seconds: Bounded planner-recommended wait/settle delay.
    """

    thought_process: str
    action_type: str
    label: str
    x: float
    y: float
    confidence: float
    reason: str
    source: str = "ai"
    target_id: str = ""
    delay_seconds: float = 1.0

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
            x=_safe_float(raw.get("x"), math.nan),
            y=_safe_float(raw.get("y"), math.nan),
            confidence=_safe_float(raw.get("confidence"), 0.0),
            reason=str(raw.get("reason", "")),
            source=source,
            target_id=str(raw.get("target_id", "")),
            delay_seconds=_clamp_delay(raw.get("delay_seconds", 1.0)),
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
            "target_id": {"type": "string"},
            "label": {"type": "string"},
            "confidence": {"type": "number"},
            "delay_seconds": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": [
            "thought_process",
            "action_type",
            "target_id",
            "label",
            "confidence",
            "delay_seconds",
            "reason",
        ],
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
    def _target_from_mapping(target_id, source, raw):
        label = str(raw.get("label", raw.get("text", ""))).strip()
        x = _safe_float(raw.get("x"))
        y = _safe_float(raw.get("y"))
        width = _safe_float(raw.get("width"), 0.0)
        height = _safe_float(raw.get("height"), 0.0)
        confidence = _safe_float(raw.get("confidence"), 0.0)
        if not label or not math.isfinite(x) or not math.isfinite(y):
            return None
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            return None
        return PlannerTarget(
            target_id=target_id,
            label=label,
            x=x,
            y=y,
            width=max(0.0, min(1.0, width)) if math.isfinite(width) else 0.0,
            height=max(0.0, min(1.0, height)) if math.isfinite(height) else 0.0,
            confidence=max(0.0, min(1.0, confidence)) if math.isfinite(confidence) else 0.0,
            source=source,
        )

    @classmethod
    def build_targets(cls, detections=None, ocr_regions=None):
        """Build stable per-observation target IDs from detector and OCR outputs."""
        targets = []
        for index, detection in enumerate(detections or [], start=1):
            raw = detection.to_dict() if hasattr(detection, "to_dict") else detection
            if not isinstance(raw, dict):
                continue
            target = cls._target_from_mapping(f"det_{index}", "detector", raw)
            if target:
                targets.append(target)

        for index, region in enumerate(ocr_regions or [], start=1):
            raw = region.to_dict() if hasattr(region, "to_dict") else region
            if not isinstance(raw, dict):
                continue
            target = cls._target_from_mapping(f"ocr_{index}", "ocr", raw)
            if target:
                targets.append(target)
        return targets

    @staticmethod
    def resolve_target_decision(decision, targets):
        if not isinstance(decision, PlannerDecision):
            return None
        if decision.action_type != "click":
            return decision

        target_by_id = {target.target_id: target for target in targets or []}
        target = target_by_id.get(decision.target_id)
        if not target:
            LOGGER.warning("Dynamic planner rejected click with unknown target_id: %s", decision.target_id)
            return None

        return PlannerDecision(
            thought_process=decision.thought_process,
            action_type=decision.action_type,
            label=decision.label or target.label,
            x=target.x,
            y=target.y,
            confidence=decision.confidence,
            reason=decision.reason,
            source=decision.source,
            target_id=target.target_id,
            delay_seconds=decision.delay_seconds,
        )

    @staticmethod
    def decision_from_memory(entry, targets=None):
        """Convert a VisionMemory entry into a planner decision.

        Args:
            entry: Memory entry returned by `VisionMemory.find`.

        Returns:
            PlannerDecision: A memory-sourced click/wait/stop decision.
        """
        point = entry.get("normalized_point", {}) if entry else {}
        entry_label = str(entry.get("label", "memory") if entry else "memory")
        target = None
        if targets:
            same_label_targets = [
                candidate
                for candidate in targets
                if candidate.label.lower() == entry_label.lower()
            ]
            candidates = same_label_targets or list(targets)
            memory_x = _safe_float(point.get("x"), 0.0)
            memory_y = _safe_float(point.get("y"), 0.0)
            target = min(
                candidates,
                key=lambda candidate: abs(candidate.x - memory_x) + abs(candidate.y - memory_y),
            )
        return PlannerDecision(
            thought_process="Recovered from local visual memory.",
            action_type=entry.get("action_type", "click"),
            label=target.label if target else entry_label,
            x=float(target.x if target else point.get("x", math.nan)),
            y=float(target.y if target else point.get("y", math.nan)),
            confidence=float(entry.get("confidence", 0.0)),
            reason=f"Matched prior screen with similarity {entry.get('similarity', 0):.3f}.",
            source="memory",
            target_id=target.target_id if target else "",
            delay_seconds=_clamp_delay(entry.get("delay_seconds", 1.0)),
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
        if not math.isfinite(decision.delay_seconds) or not 0.0 <= decision.delay_seconds <= MAX_PLANNER_DELAY_SECONDS:
            return False
        if decision.action_type == "click":
            return (
                bool(decision.target_id)
                and math.isfinite(decision.x)
                and math.isfinite(decision.y)
                and decision.confidence >= MIN_PLANNER_CONFIDENCE
                and 0.0 <= decision.x <= 1.0
                and 0.0 <= decision.y <= 1.0
            )
        return True

    def _request_decision(self, context, screenshot_path, detections, ocr_text, goal, targets):
        """Ask OpenAI for a single next action in strict JSON format.

        Args:
            context: Current OSROKBOT runtime context.
            screenshot_path: Screenshot file to send as vision input.
            detections: YOLO detections visible on screen.
            ocr_text: OCR text extracted from the screenshot.
            goal: Natural-language mission from the Commander UI.
            targets: Local detector/OCR targets the model may reference.

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
        target_payload = [target.to_prompt_dict() for target in targets]
        history = getattr(context, "state_history", [])[-10:] if context else []
        prompt = (
            "You control a guarded Rise of Kingdoms automation planner. Return one safe next action. "
            "Do not solve captchas. Do not invent UI controls. For click actions, choose exactly one target_id "
            "from the visible_targets list. Do not return x or y coordinates. Prefer wait or stop if no listed "
            "target is safe.\n\n"
            f"Goal: {goal}\n"
            f"Visible detector labels: {labels}\n"
            f"Visible targets: {json.dumps(target_payload, ensure_ascii=True)}\n"
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
            return self.resolve_target_decision(PlannerDecision.from_mapping(raw, source="ai"), targets)
        except Exception as exc:
            LOGGER.error(f"Dynamic planner request failed: {exc}")
            return None

    def plan_next(self, context, screenshot_path, detections, ocr_text, goal, ocr_regions=None):
        """Return the next safe planner decision for the current screen.

        Args:
            context: Current OSROKBOT runtime context.
            screenshot_path: Current screenshot path.
            detections: Object detector outputs for the screenshot.
            ocr_text: OCR text from the screenshot.
            goal: Natural-language mission prompt.
            ocr_regions: Optional OCR regions with normalized boxes.

        Returns:
            PlannerDecision | None: A validated memory or AI decision. None is
            returned when no safe decision is available.
        """
        labels = self._visible_labels(detections)
        targets = self.build_targets(detections, ocr_regions)
        memory_entry = self.memory.find(screenshot_path, labels)
        if memory_entry:
            decision = self.decision_from_memory(memory_entry, targets)
            if self.validate_decision(decision):
                return decision

        decision = self._request_decision(context, screenshot_path, detections, ocr_text, goal, targets)
        if not self.validate_decision(decision):
            LOGGER.warning("Dynamic planner rejected an invalid or low-confidence decision.")
            return None
        return decision
