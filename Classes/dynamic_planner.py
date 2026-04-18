"""Planner-side contracts for one guarded OSROKBOT decision.

This module owns the side-effect-free planning boundary between current
perception and input execution. It normalizes detector and OCR targets,
validates strict model JSON, and routes OpenAI Responses API calls through a
dedicated async transport so the runtime-facing planner API remains
synchronous.

Threading:
    `AsyncPlannerTransport` owns a background event loop on a daemon thread and
    must be closed during runtime teardown.

Side Effects:
    The planner may read local visual memory and call the OpenAI API. It must
    not send hardware input, mutate game state, or write correction data.
"""

import asyncio
import copy
import inspect
import json
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from config_manager import ConfigManager
from encoding_utils import image_data_url, safe_json_loads
from logging_config import get_logger
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from runtime_contracts import PlannerTransport
from vision_memory import VisionMemory

LOGGER = get_logger(__name__)


ALLOWED_ACTION_TYPES = {"click", "wait", "stop", "drag", "long_press", "key", "type"}
DEFAULT_MODEL = "gpt-5.4-mini"
MIN_PLANNER_CONFIDENCE = 0.70
MIN_L1_REVIEW_CONFIDENCE = 0.10
MAX_PLANNER_DELAY_SECONDS = 10.0
REQUEST_POLL_SECONDS = 0.1
MAX_REQUEST_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0


def _safe_float(value, default=math.nan):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_delay(value):
    delay = _safe_float(value, 1.0)
    if not math.isfinite(delay):
        return 1.0
    return max(0.0, min(MAX_PLANNER_DELAY_SECONDS, delay))


def _record_runtime_timing(
    context: Any,
    stage: str,
    started_at: float,
    *,
    detail: str = "",
) -> None:
    record_timing = getattr(context, "record_runtime_timing", None)
    if callable(record_timing):
        record_timing(stage, (time.perf_counter() - started_at) * 1000.0, detail=detail)


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


class PlannerLLMDecision(BaseModel):
    """Strict model-facing planner response.

    The model may select a local target by ID, but it must not return raw
    coordinates. Coordinates are resolved from current detector/OCR targets.

    Extended action types:
    - click: Click a target by ID.
    - wait: Pause and observe again.
    - stop: Stop the automation run.
    - drag: Drag from one target to another (or in a direction).
    - long_press: Long-press a target.
    - key: Press a keyboard key.
    - type: Type text into an input field.
    """

    model_config = ConfigDict(extra="forbid")

    thought_process: str
    action_type: Literal["click", "wait", "stop", "drag", "long_press", "key", "type"]
    target_id: str
    label: str
    confidence: float
    delay_seconds: float
    reason: str
    # Extended fields for new action types.
    end_target_id: str = ""
    key_name: str = ""
    text_content: str = ""
    drag_direction: str = ""

    @field_validator("action_type", mode="before")
    @classmethod
    def _normalize_action_type(cls, value):
        return str(value or "wait").lower()

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value):
        return _safe_float(value, 0.0)

    @field_validator("delay_seconds", mode="before")
    @classmethod
    def _coerce_delay(cls, value):
        return _clamp_delay(value)

    @model_validator(mode="after")
    def _require_target_for_click(self):
        if self.action_type == "click" and not self.target_id:
            raise ValueError("click decisions must reference a target_id")
        if self.action_type == "drag" and not self.target_id:
            raise ValueError("drag decisions must reference a target_id")
        if self.action_type == "long_press" and not self.target_id:
            raise ValueError("long_press decisions must reference a target_id")
        if self.action_type == "key" and not self.key_name:
            raise ValueError("key decisions must specify a key_name")
        if self.action_type == "type" and not self.text_content:
            raise ValueError("type decisions must specify text_content")
        return self


def _openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a Responses API-compatible strict object schema.

    OpenAI structured outputs require each object property to be listed in
    ``required`` and reject Pydantic's default metadata. Runtime validation
    still applies the Python-side defaults when parsing historical/test
    payloads that omit the extended action fields.
    """

    cleaned_schema = copy.deepcopy(schema)

    def normalize(node: Any) -> None:
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties.keys())
            node["additionalProperties"] = False
            for child in properties.values():
                normalize(child)
        for key in ("$defs", "items", "anyOf", "oneOf", "allOf"):
            value = node.get(key)
            if isinstance(value, dict):
                for child in value.values():
                    normalize(child)
            elif isinstance(value, list):
                for child in value:
                    normalize(child)

    normalize(cleaned_schema)
    return cleaned_schema


class PlannerDecision(BaseModel):
    """One structured action proposed by the AI planner.

    Attributes:
        thought_process: Short model explanation shown only for debugging.
        action_type: One of `click`, `wait`, `stop`, `drag`, `long_press`, `key`, `type`.
        label: Human-readable target name, such as `gather button`.
        x: Normalized horizontal coordinate from 0.0 to 1.0.
        y: Normalized vertical coordinate from 0.0 to 1.0.
        confidence: Model confidence from 0.0 to 1.0.
        reason: Short user-facing reason for the action.
        source: Where the decision came from, usually `ai` or `memory`.
        target_id: Local detector/OCR target ID used to resolve click coordinates.
        delay_seconds: Bounded planner-recommended wait/settle delay.
        end_target_id: For drag actions, the target to drag to.
        end_x: For drag actions, the resolved end x coordinate.
        end_y: For drag actions, the resolved end y coordinate.
        key_name: For key actions, the key to press.
        text_content: For type actions, the text to type.
        drag_direction: For drag actions without end target, direction hint.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=True)

    _POSITIONAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "thought_process",
        "action_type",
        "label",
        "x",
        "y",
        "confidence",
        "reason",
        "source",
        "target_id",
        "delay_seconds",
    )

    thought_process: str = ""
    action_type: str = "wait"
    label: str = ""
    x: float = Field(default=math.nan)
    y: float = Field(default=math.nan)
    confidence: float = 0.0
    reason: str = ""
    source: str = "ai"
    target_id: str = ""
    delay_seconds: float = 1.0
    end_target_id: str = ""
    end_x: float = Field(default=math.nan)
    end_y: float = Field(default=math.nan)
    key_name: str = ""
    text_content: str = ""
    drag_direction: str = ""

    def __init__(self, *args, **data):
        if args:
            positional_fields = type(self)._POSITIONAL_FIELDS
            if len(args) > len(positional_fields):
                raise TypeError(f"PlannerDecision expected at most {len(positional_fields)} positional arguments")
            for name, value in zip(positional_fields, args, strict=False):
                if name in data:
                    raise TypeError(f"PlannerDecision got multiple values for argument '{name}'")
                data[name] = value
        super().__init__(**data)

    @field_validator("action_type", mode="before")
    @classmethod
    def _normalize_action_type(cls, value):
        return str(value or "wait").lower()

    @field_validator("x", "y", "end_x", "end_y", mode="before")
    @classmethod
    def _coerce_coordinate(cls, value):
        return _safe_float(value, math.nan)

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value):
        return _safe_float(value, 0.0)

    @field_validator("delay_seconds", mode="before")
    @classmethod
    def _coerce_delay(cls, value):
        return _clamp_delay(value)

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
            thought_process=raw.get("thought_process", ""),
            action_type=raw.get("action_type", "wait"),
            label=raw.get("label", ""),
            x=raw.get("x", math.nan),
            y=raw.get("y", math.nan),
            confidence=raw.get("confidence", 0.0),
            reason=raw.get("reason", ""),
            source=source,
            target_id=raw.get("target_id", ""),
            delay_seconds=raw.get("delay_seconds", 1.0),
            end_target_id=raw.get("end_target_id", ""),
            end_x=raw.get("end_x", math.nan),
            end_y=raw.get("end_y", math.nan),
            key_name=raw.get("key_name", ""),
            text_content=raw.get("text_content", ""),
            drag_direction=raw.get("drag_direction", ""),
        )

    @classmethod
    def from_llm_decision(cls, decision, source="ai"):
        """Create an unresolved internal decision from a validated LLM payload."""
        return cls(
            thought_process=decision.thought_process,
            action_type=decision.action_type,
            label=decision.label,
            confidence=decision.confidence,
            reason=decision.reason,
            source=source,
            target_id=decision.target_id,
            delay_seconds=decision.delay_seconds,
            end_target_id=getattr(decision, "end_target_id", ""),
            key_name=getattr(decision, "key_name", ""),
            text_content=getattr(decision, "text_content", ""),
            drag_direction=getattr(decision, "drag_direction", ""),
        )

    def to_dict(self):
        """Return a JSON-serializable dictionary for Context and UI payloads."""
        return self.model_dump()


class AsyncPlannerTransport:
    """Run planner network I/O on a dedicated asyncio loop.

    This transport keeps `DynamicPlanner` synchronous for the rest of the
    runtime while isolating OpenAI Responses API calls, retry backoff, and
    async-client shutdown on a dedicated daemon thread.
    """

    def __init__(
        self,
        api_key: str,
        is_transient_error: Callable[[Exception], bool],
        poll_seconds: float = REQUEST_POLL_SECONDS,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._is_transient_error = is_transient_error
        self._poll_seconds = poll_seconds
        self._ready = threading.Event()
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._startup_error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run_loop,
            name="OSROKBOT-PlannerAsync",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise RuntimeError("Planner async transport failed to start") from self._startup_error

    def _run_loop(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
        except (OSError, RuntimeError) as exc:
            self._startup_error = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            finally:
                self._loop.close()

    async def _create_response(self, request_payload: dict[str, Any]) -> Any:
        response = self._client.responses.create(**request_payload)
        if inspect.isawaitable(response):
            return await response
        return response

    async def _close_client(self) -> None:
        close = getattr(self._client, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    def _submit(self, request_payload: dict[str, Any]):
        if self._closed or self._loop is None:
            raise RuntimeError("Planner async transport is closed")
        return asyncio.run_coroutine_threadsafe(self._create_response(request_payload), self._loop)

    def _wait_for_future(self, future, should_cancel: Callable[[], bool]) -> Any | None:
        while True:
            if should_cancel():
                future.cancel()
                return None
            try:
                return future.result(timeout=self._poll_seconds)
            except FutureTimeoutError:
                continue

    @staticmethod
    def _interruptible_sleep(should_cancel: Callable[[], bool], seconds: float, poll_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if should_cancel():
                return False
            time.sleep(min(poll_seconds, deadline - time.monotonic()))
        return not should_cancel()

    def request(self, request_payload: dict[str, Any], should_cancel: Callable[[], bool]) -> Any | None:
        """Submit one planner request and retry transient failures.

        Args:
            request_payload: OpenAI Responses API payload for a single planner
                decision request.
            should_cancel: Callback that aborts the request while the runtime is
                pausing or stopping.

        Returns:
            Any | None: Raw Responses API object when a request completes, or
            `None` when cancellation wins before a response arrives.

        Raises:
            Exception: Re-raises the last non-transient API or transport error
            after retries are exhausted.
        """
        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            if should_cancel():
                return None
            future = self._submit(request_payload)
            try:
                return self._wait_for_future(future, should_cancel)
            except Exception as exc:
                if not self._is_transient_error(exc) or attempt >= MAX_REQUEST_ATTEMPTS:
                    raise
                backoff = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                LOGGER.warning("Dynamic planner transient OpenAI failure; retrying: %s", exc)
                if not self._interruptible_sleep(should_cancel, backoff, self._poll_seconds):
                    return None
        return None

    def close(self) -> None:
        """Stop the background loop and release the async OpenAI client."""
        if self._closed:
            return
        self._closed = True
        if self._loop is not None:
            with suppress(FutureTimeoutError, RuntimeError):
                asyncio.run_coroutine_threadsafe(self._close_client(), self._loop).result(timeout=1.0)
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)


class DynamicPlanner:
    """Plan one side-effect-free automation step from current observation data.

    `DynamicPlanner` sits between perception and execution. It can reuse local
    successful memory, ask the OpenAI Responses API for one strict JSON
    decision, resolve local target IDs into normalized coordinates, and reject
    unsafe or unsupported actions before the runtime sees them.

    Collaborators:
        `VisionMemory` provides memory-first reuse.
        `AsyncPlannerTransport` isolates network I/O on a background event
        loop.
        `DynamicPlannerAction` remains responsible for approvals, memory
        writes, and hardware input.

    Invariants:
        - Never executes hardware input.
        - Never returns raw model coordinates from the model.
        - Never mutates UI or game state.
    """

    SCHEMA = _openai_strict_schema(PlannerLLMDecision.model_json_schema())

    def __init__(self, config=None, memory=None, transport: PlannerTransport | None = None, transport_factory=None):
        """Initialize the planner using ConfigManager and optional memory.

        Args:
            config: Optional ConfigManager-like object for API keys/model name.
            memory: Optional VisionMemory instance used before OpenAI calls.
            transport: Optional prebuilt planner transport for tests or custom
                enterprise transports.
            transport_factory: Optional factory used to create the default
                async OpenAI transport.

        Raises:
            RuntimeError: If the default async planner transport cannot start.
        """
        self.config = config or ConfigManager()
        api_key = self.config.get("OPENAI_KEY") or self.config.get("OPENAI_API_KEY")
        self.client = OpenAI(api_key=api_key) if api_key else None
        self.model = self.config.get("OPENAI_VISION_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
        self.memory = memory or VisionMemory()
        factory = transport_factory or AsyncPlannerTransport
        self._transport: PlannerTransport | None = transport
        if self._transport is None and api_key:
            self._transport = factory(api_key=api_key, is_transient_error=self._is_transient_openai_error)

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
        """Resolve planner target IDs into current normalized coordinates.

        Args:
            decision: Candidate planner decision using local `target_id`
                references.
            targets: Current detector and OCR targets from this observation.

        Returns:
            PlannerDecision | None: A coordinate-resolved decision, or `None`
            when the target references are missing or invalid for the current
            screen.
        """
        if not isinstance(decision, PlannerDecision):
            return None
        if decision.action_type not in {"click", "drag", "long_press"}:
            return decision

        target_by_id = {target.target_id: target for target in targets or []}
        target = target_by_id.get(decision.target_id)
        if not target:
            LOGGER.warning("Dynamic planner rejected %s with unknown target_id: %s",
                           decision.action_type, decision.target_id)
            return None

        updates = {
            "label": decision.label or target.label,
            "x": target.x,
            "y": target.y,
        }

        # Resolve end target for drag actions.
        if decision.action_type == "drag" and decision.end_target_id:
            end_target = target_by_id.get(decision.end_target_id)
            if end_target:
                updates["end_x"] = end_target.x
                updates["end_y"] = end_target.y
            else:
                LOGGER.warning("Dynamic planner rejected drag with unknown end_target_id: %s",
                               decision.end_target_id)
                return None

        return PlannerDecision(
            thought_process=decision.thought_process,
            action_type=decision.action_type,
            label=updates.get("label", decision.label),
            x=updates.get("x", decision.x),
            y=updates.get("y", decision.y),
            confidence=decision.confidence,
            reason=decision.reason,
            source=decision.source,
            target_id=decision.target_id,
            delay_seconds=decision.delay_seconds,
            end_target_id=decision.end_target_id,
            end_x=updates.get("end_x", decision.end_x),
            end_y=updates.get("end_y", decision.end_y),
            key_name=decision.key_name,
            text_content=decision.text_content,
            drag_direction=decision.drag_direction,
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
        if decision.action_type in {"click", "long_press"}:
            return (
                bool(decision.target_id)
                and math.isfinite(decision.x)
                and math.isfinite(decision.y)
                and decision.confidence >= MIN_PLANNER_CONFIDENCE
                and 0.0 <= decision.x <= 1.0
                and 0.0 <= decision.y <= 1.0
            )
        if decision.action_type == "drag":
            valid_start = (
                bool(decision.target_id)
                and math.isfinite(decision.x)
                and math.isfinite(decision.y)
                and 0.0 <= decision.x <= 1.0
                and 0.0 <= decision.y <= 1.0
            )
            # Drag needs either end coordinates or a direction hint.
            valid_end = (
                (math.isfinite(decision.end_x) and math.isfinite(decision.end_y)
                 and 0.0 <= decision.end_x <= 1.0 and 0.0 <= decision.end_y <= 1.0)
                or bool(decision.drag_direction)
            )
            return valid_start and valid_end and decision.confidence >= MIN_PLANNER_CONFIDENCE
        if decision.action_type == "key":
            return bool(decision.key_name) and decision.confidence >= MIN_PLANNER_CONFIDENCE
        if decision.action_type == "type":
            return bool(decision.text_content) and decision.confidence >= MIN_PLANNER_CONFIDENCE
        return True

    @staticmethod
    def decision_rejection_reason(decision):
        """Return a concise reason explaining why a decision is unsafe."""

        if not isinstance(decision, PlannerDecision):
            return "not_a_planner_decision"
        if decision.action_type not in ALLOWED_ACTION_TYPES:
            return f"unsupported_action:{decision.action_type}"
        if not math.isfinite(decision.confidence):
            return "confidence_not_finite"
        if decision.confidence < MIN_PLANNER_CONFIDENCE and decision.action_type in {"click", "drag", "long_press", "key", "type"}:
            return f"confidence_below_threshold:{decision.confidence:.3f}<{MIN_PLANNER_CONFIDENCE:.3f}"
        if not math.isfinite(decision.delay_seconds) or not 0.0 <= decision.delay_seconds <= MAX_PLANNER_DELAY_SECONDS:
            return f"delay_out_of_bounds:{decision.delay_seconds}"
        if decision.action_type in {"click", "long_press"}:
            if not decision.target_id:
                return "missing_target_id"
            if not math.isfinite(decision.x) or not math.isfinite(decision.y):
                return "target_coordinates_not_finite"
            if not 0.0 <= decision.x <= 1.0 or not 0.0 <= decision.y <= 1.0:
                return f"target_coordinates_out_of_bounds:{decision.x:.3f},{decision.y:.3f}"
        if decision.action_type == "drag":
            if not decision.target_id:
                return "missing_target_id"
            if not math.isfinite(decision.x) or not math.isfinite(decision.y):
                return "drag_start_not_finite"
            if not 0.0 <= decision.x <= 1.0 or not 0.0 <= decision.y <= 1.0:
                return f"drag_start_out_of_bounds:{decision.x:.3f},{decision.y:.3f}"
            valid_end = (
                math.isfinite(decision.end_x)
                and math.isfinite(decision.end_y)
                and 0.0 <= decision.end_x <= 1.0
                and 0.0 <= decision.end_y <= 1.0
            )
            if not valid_end and not decision.drag_direction:
                return "drag_missing_end_target_or_direction"
        if decision.action_type == "key" and not decision.key_name:
            return "missing_key_name"
        if decision.action_type == "type" and not decision.text_content:
            return "missing_text_content"
        return "unknown"

    @staticmethod
    def _autonomy_level(context):
        try:
            return int(getattr(context, "planner_autonomy_level", 1))
        except (TypeError, ValueError):
            return 1

    def _l1_review_min_confidence(self):
        try:
            return float(self.config.get("PLANNER_L1_REVIEW_MIN_CONFIDENCE", MIN_L1_REVIEW_CONFIDENCE))
        except (TypeError, ValueError):
            return MIN_L1_REVIEW_CONFIDENCE

    def _is_l1_reviewable_pointer_decision(self, context, decision, rejection_reason):
        if self._autonomy_level(context) != 1:
            return False
        if not isinstance(decision, PlannerDecision):
            return False
        if decision.action_type not in {"click", "drag", "long_press"}:
            return False
        if not str(rejection_reason).startswith("confidence_below_threshold:"):
            return False
        if decision.confidence < self._l1_review_min_confidence():
            return False

        structurally_safe = decision.model_copy(update={"confidence": MIN_PLANNER_CONFIDENCE})
        return self.validate_decision(structurally_safe)

    @staticmethod
    def _record_planner_rejection(context, decision, reason):
        session_logger = getattr(context, "session_logger", None)
        if not session_logger or not hasattr(session_logger, "record_planner_rejection"):
            return
        decision_detail = decision.to_dict() if isinstance(decision, PlannerDecision) else {}
        session_logger.record_planner_rejection(
            reason=reason,
            action_type=decision_detail.get("action_type", ""),
            label=decision_detail.get("label", ""),
            target_id=decision_detail.get("target_id", ""),
            confidence=decision_detail.get("confidence", ""),
        )

    @staticmethod
    def _request_interrupted(context):
        bot = getattr(context, "bot", None) if context else None
        if not bot:
            return False
        stop_event = getattr(bot, "stop_event", None)
        pause_event = getattr(bot, "pause_event", None)
        return bool(
            (stop_event is not None and stop_event.is_set())
            or (pause_event is not None and pause_event.is_set())
        )

    @staticmethod
    def _is_transient_openai_error(exc):
        if isinstance(exc, APIConnectionError | APITimeoutError | InternalServerError | RateLimitError):
            return True
        if isinstance(exc, AuthenticationError | BadRequestError | PermissionDeniedError):
            return False
        if isinstance(exc, APIStatusError):
            status_code = int(getattr(exc, "status_code", 0) or 0)
            return status_code in {408, 409, 429} or status_code >= 500
        return False

    def _request_response_with_retries(self, context, request_payload):
        if not self._transport:
            LOGGER.warning("Dynamic planner unavailable: async planner transport is not configured.")
            return None
        return self._transport.request(request_payload, lambda: self._request_interrupted(context))

    def close(self):
        """Release the planner transport owned by this planner instance."""
        if self._transport:
            self._transport.close()
            self._transport = None

    def __del__(self):
        with suppress(Exception):
            self.close()

    @staticmethod
    def _build_prompt(goal, labels, target_payload, ocr_text, history,
                      resource_context=None, stuck_warning="",
                      screen_changed=True):
        """Build the full planner prompt with all available context.

        Args:
            goal: Natural-language mission (or focused sub-goal).
            labels: Visible detector labels.
            target_payload: JSON-serializable target list.
            ocr_text: OCR text from the screenshot.
            history: Recent state history entries.
            resource_context: Optional dict with march_slots, action_points, etc.
            stuck_warning: Warning text from ScreenChangeDetector.
            screen_changed: Whether the screen changed since the last cycle.
        """
        parts = [
            "You control a guarded Rise of Kingdoms automation planner. Return one safe next action. "
            "Do not solve captchas. Do not invent UI controls. For click, drag, or long_press actions, "
            "choose exactly one target_id from the visible_targets list. Do not return x or y coordinates. "
            "Prefer wait or stop if no listed target is safe.\n",
            "Available action types:\n"
            "- click: Click a visible target by target_id.\n"
            "- wait: Pause briefly and re-observe.\n"
            "- stop: Stop the current automation run.\n"
            "- drag: Drag from target_id to end_target_id, or specify drag_direction "
            "(up/down/left/right) for map panning.\n"
            "- long_press: Long-press a target by target_id.\n"
            "- key: Press a keyboard key (set key_name, e.g., 'escape', 'space').\n"
            "- type: Type text into the focused input (set text_content).\n",
            f"\nGoal: {goal}",
            f"\nVisible detector labels: {labels}",
            f"\nVisible targets: {json.dumps(target_payload, ensure_ascii=True)}",
            f"\nOCR text: {ocr_text or ''}",
        ]

        if resource_context:
            parts.append(f"\nResource status: {json.dumps(resource_context, ensure_ascii=True)}")

        if not screen_changed:
            parts.append("\nNote: The screen has NOT changed since the last action.")

        if stuck_warning:
            parts.append(f"\n{stuck_warning}")

        parts.append(f"\nRecent history: {json.dumps(history, ensure_ascii=True)}")

        return "\n".join(parts)

    def _request_decision(self, context, screenshot_path, detections, ocr_text, goal,
                          targets, resource_context=None, stuck_warning="",
                          screen_changed=True):
        """Ask OpenAI for a single next action in strict JSON format.

        Args:
            context: Current OSROKBOT runtime context.
            screenshot_path: Screenshot file to send as vision input.
            detections: YOLO detections visible on screen.
            ocr_text: OCR text extracted from the screenshot.
            goal: Natural-language mission (or focused sub-goal) from the UI.
            targets: Local detector/OCR targets the model may reference.
            resource_context: Optional resource awareness dict.
            stuck_warning: Warning from ScreenChangeDetector.
            screen_changed: Whether screen changed since last cycle.

        Returns:
            PlannerDecision | None: Parsed model decision, or None if the
            request fails.
        """
        if not self._transport:
            LOGGER.warning("Dynamic planner unavailable: OPENAI_KEY/OPENAI_API_KEY is not configured.")
            return None

        labels = self._visible_labels(detections)
        target_payload = [target.to_prompt_dict() for target in targets]
        history = getattr(context, "state_history", [])[-10:] if context else []

        prompt = self._build_prompt(
            goal=goal,
            labels=labels,
            target_payload=target_payload,
            ocr_text=ocr_text,
            history=history,
            resource_context=resource_context,
            stuck_warning=stuck_warning,
            screen_changed=screen_changed,
        )

        started_at = time.perf_counter()
        try:
            request_payload = {
                "model": self.model,
                "instructions": "Return only the strict JSON object requested by the schema.",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": image_data_url(screenshot_path)},
                        ],
                    }
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "osrokbot_planner_decision",
                        "strict": True,
                        "schema": self.SCHEMA,
                    }
                },
            }
            response = self._request_response_with_retries(context, request_payload)
            if response is None:
                _record_runtime_timing(context, "planner_request", started_at, detail="cancelled")
                return None
            raw = safe_json_loads(response.output_text)
            llm_decision = PlannerLLMDecision.model_validate(raw)
            decision = self.resolve_target_decision(
                PlannerDecision.from_llm_decision(llm_decision, source="ai"),
                targets,
            )
            _record_runtime_timing(
                context,
                "planner_request",
                started_at,
                detail=f"action={decision.action_type}" if decision else "action=unresolved",
            )
            return decision
        except (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            PermissionDeniedError,
            RateLimitError,
            RuntimeError,
            TypeError,
            ValidationError,
            ValueError,
        ) as exc:
            _record_runtime_timing(
                context,
                "planner_request",
                started_at,
                detail=f"error={type(exc).__name__}",
            )
            LOGGER.error(f"Dynamic planner request failed: {exc}")
            return None

    def plan_next(self, context, screenshot_path, detections, ocr_text, goal,
                  ocr_regions=None, resource_context=None, stuck_warning="",
                  screen_changed=True):
        """Return the next safe planner decision for the current screen.

        Args:
            context: Current OSROKBOT runtime context.
            screenshot_path: Current screenshot path.
            detections: Object detector outputs for the screenshot.
            ocr_text: OCR text from the screenshot.
            goal: Natural-language mission prompt (or focused sub-goal).
            ocr_regions: Optional OCR regions with normalized boxes.
            resource_context: Optional resource awareness dict.
            stuck_warning: Warning from ScreenChangeDetector.
            screen_changed: Whether screen changed since last cycle.

        Returns:
            PlannerDecision | None: A validated memory or AI decision. None is
            returned when no safe decision is available.
        """
        labels = self._visible_labels(detections)
        targets = self.build_targets(detections, ocr_regions)
        memory_started_at = time.perf_counter()
        try:
            memory_entry = self.memory.find(screenshot_path, labels)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            _record_runtime_timing(
                context,
                "planner_memory_lookup",
                memory_started_at,
                detail=f"error={type(exc).__name__}",
            )
            LOGGER.warning("Dynamic planner memory lookup failed: %s", exc)
            memory_entry = None
        else:
            _record_runtime_timing(
                context,
                "planner_memory_lookup",
                memory_started_at,
                detail="hit" if memory_entry else "miss",
            )
        if memory_entry:
            decision = self.decision_from_memory(memory_entry, targets)
            if self.validate_decision(decision):
                return decision

        decision = self._request_decision(
            context, screenshot_path, detections, ocr_text, goal, targets,
            resource_context=resource_context,
            stuck_warning=stuck_warning,
            screen_changed=screen_changed,
        )
        if not self.validate_decision(decision):
            reason = self.decision_rejection_reason(decision)
            decision_detail = decision.to_dict() if isinstance(decision, PlannerDecision) else {}
            if self._is_l1_reviewable_pointer_decision(context, decision, reason):
                LOGGER.warning(
                    "Dynamic planner sent low-confidence decision to L1 Fix review: reason=%s action=%s target_id=%s label=%s confidence=%s",
                    reason,
                    decision_detail.get("action_type", ""),
                    decision_detail.get("target_id", ""),
                    decision_detail.get("label", ""),
                    decision_detail.get("confidence", ""),
                )
                return decision.model_copy(update={"source": "ai_review"})
            LOGGER.warning(
                "Dynamic planner rejected decision: reason=%s action=%s target_id=%s label=%s confidence=%s",
                reason,
                decision_detail.get("action_type", ""),
                decision_detail.get("target_id", ""),
                decision_detail.get("label", ""),
                decision_detail.get("confidence", ""),
            )
            self._record_planner_rejection(context, decision, reason)
            return None
        return decision
