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
import random
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from config_manager import ConfigManager
from context import record_stage_timing
from encoding_utils import image_data_url, safe_json_loads
from helpers import UIMap
from logging_config import get_logger
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
from planner_decision_policy import (
    MAX_PLANNER_DELAY_SECONDS,
    MIN_L1_REVIEW_CONFIDENCE,
    MIN_PLANNER_CONFIDENCE,
    decision_verdict,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from runtime_contracts import PlannerTransport
from vision_memory import VisionMemory

LOGGER = get_logger(__name__)


DEFAULT_MODEL = "gpt-5.4-mini"
REQUEST_POLL_SECONDS = 0.1
MAX_REQUEST_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_JITTER_RATIO = 0.2
PLANNER_CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
PLANNER_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 30.0
RESOURCE_GOAL_KEYWORDS = ("gather", "farm", "resource", "harvest", "mine", "collect")
RESOURCE_TEXT_KEYWORDS = ("food", "wood", "stone", "gold", "gem", "ore", "gather", "farm", "resource", "node")
OCR_UI_BLACKLIST = (
    "technology",
    "research",
    "apprentice",
    "civilization",
    "barbarian",
    "swordsman",
    "blacksmith",
    "land of",
    "multi",
    "space",
    "mail",
    "alliance",
    "event",
    "quest",
    "mission",
    "vip",
)
PLANNER_MEMORY_LIMIT = 5
MAP_GOAL_KEYWORDS = ("open the world map", "world map", "map view", "switch to map")
SEARCH_INTERFACE_GOAL_KEYWORDS = (
    "search interface",
    "search function",
    "search menu",
    "resource search",
    "find nearby resource",
)
MAP_LABEL_HINTS = {
    "searchaction",
    "gatheraction",
    "attackaction",
    "marchaction",
    "smallmarchaction",
    "scoutaction",
    "rallyaction",
}
CITY_LABEL_HINTS = {
    "newtroopaction",
    "useaction",
    "trainaction",
    "upgradeaction",
    "buildaction",
}
BLOCKER_LABEL_HINTS = {"confirm", "escx", "captcha", "captchachest", "captcha_chest"}
RESOURCE_REVIEW_SCREEN_KEYWORDS = (
    "search",
    "gather",
    "march",
    "resource point",
    "alliance resource",
    "occupy",
)
SEARCH_PANEL_RESOURCE_KEYWORDS = ("food", "wood", "stone", "gold", "gem")
CITY_SCREEN_TEXT_KEYWORDS = (
    "technology research",
    "blacksmith apprentice",
    "land of civilization",
    "feudal age",
    "machinery",
    "battering ram",
    "heavy cavalry",
    "spearman",
)


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
        """Return the model-facing target payload for planner prompts."""

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


class PlannerTransportCircuitOpenError(RuntimeError):
    """Raised when the planner transport circuit breaker is open."""


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
        *,
        max_attempts: int = MAX_REQUEST_ATTEMPTS,
        retry_base_delay_seconds: float = RETRY_BASE_DELAY_SECONDS,
        retry_jitter_ratio: float = RETRY_JITTER_RATIO,
        circuit_breaker_failure_threshold: int = PLANNER_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        circuit_breaker_cooldown_seconds: float = PLANNER_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        random_source: Callable[[], float] | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._is_transient_error = is_transient_error
        self._poll_seconds = poll_seconds
        self._max_attempts = max(1, int(max_attempts))
        self._retry_base_delay_seconds = max(0.0, float(retry_base_delay_seconds))
        self._retry_jitter_ratio = max(0.0, float(retry_jitter_ratio))
        self._circuit_breaker_failure_threshold = max(1, int(circuit_breaker_failure_threshold))
        self._circuit_breaker_cooldown_seconds = max(0.0, float(circuit_breaker_cooldown_seconds))
        self._random_source = random_source or random.random
        self._ready = threading.Event()
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._startup_error: Exception | None = None
        self._state_lock = threading.Lock()
        self._consecutive_transient_failures = 0
        self._circuit_open_until = 0.0
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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(poll_seconds, remaining))
        return not should_cancel()

    def _check_circuit_breaker(self) -> None:
        with self._state_lock:
            now = time.monotonic()
            if self._circuit_open_until > now:
                remaining = self._circuit_open_until - now
                raise PlannerTransportCircuitOpenError(
                    f"Planner async transport circuit breaker is open for {remaining:.1f}s"
                )
            if self._circuit_open_until:
                self._circuit_open_until = 0.0
                self._consecutive_transient_failures = 0

    def _record_success(self) -> None:
        with self._state_lock:
            self._consecutive_transient_failures = 0
            self._circuit_open_until = 0.0

    def _record_transient_failure(self) -> float | None:
        with self._state_lock:
            self._consecutive_transient_failures += 1
            if self._consecutive_transient_failures < self._circuit_breaker_failure_threshold:
                return None
            self._circuit_open_until = time.monotonic() + self._circuit_breaker_cooldown_seconds
            return self._circuit_open_until

    def _compute_backoff_seconds(self, attempt: int) -> float:
        base_delay = self._retry_base_delay_seconds * (2 ** (attempt - 1))
        if base_delay <= 0.0 or self._retry_jitter_ratio <= 0.0:
            return base_delay
        jitter_scale = 1.0 + self._retry_jitter_ratio * ((2.0 * self._random_source()) - 1.0)
        return max(0.0, base_delay * jitter_scale)

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
        self._check_circuit_breaker()
        for attempt in range(1, self._max_attempts + 1):
            if should_cancel():
                return None
            future = self._submit(request_payload)
            try:
                response = self._wait_for_future(future, should_cancel)
                if response is not None:
                    self._record_success()
                return response
            except Exception as exc:
                if not self._is_transient_error(exc):
                    raise
                circuit_open_until = self._record_transient_failure()
                if circuit_open_until is not None:
                    cooldown = max(0.0, circuit_open_until - time.monotonic())
                    LOGGER.error(
                        "Dynamic planner circuit breaker opened after transient failure attempt=%s/%s cooldown_seconds=%.2f error=%s",
                        attempt,
                        self._max_attempts,
                        cooldown,
                        exc,
                    )
                    raise PlannerTransportCircuitOpenError(
                        f"Planner async transport circuit breaker opened for {cooldown:.1f}s"
                    ) from exc
                if attempt >= self._max_attempts:
                    raise
                backoff = self._compute_backoff_seconds(attempt)
                LOGGER.warning(
                    "Dynamic planner transient OpenAI failure; retrying attempt=%s/%s backoff_seconds=%.2f error=%s",
                    attempt,
                    self._max_attempts,
                    backoff,
                    exc,
                )
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
        return decision_verdict(decision).execution_ready

    @staticmethod
    def decision_rejection_reason(decision):
        """Return a concise reason explaining why a decision is unsafe."""
        verdict = decision_verdict(decision)
        return verdict.rejection_reason or "accepted"

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

    @staticmethod
    def _goal_prefers_resource_targets(goal):
        goal_text = str(goal or "").lower()
        return any(keyword in goal_text for keyword in RESOURCE_GOAL_KEYWORDS)

    @staticmethod
    def _goal_requests_world_map(goal):
        goal_text = str(goal or "").lower()
        return any(keyword in goal_text for keyword in MAP_GOAL_KEYWORDS)

    @staticmethod
    def _goal_requests_search_interface(goal):
        goal_text = str(goal or "").lower()
        return any(keyword in goal_text for keyword in SEARCH_INTERFACE_GOAL_KEYWORDS)

    @staticmethod
    def _normalized_text(text):
        return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()

    @classmethod
    def _text_contains_keyword(cls, text, keyword):
        normalized_text = cls._normalized_text(text)
        normalized_keyword = cls._normalized_text(keyword)
        if not normalized_text or not normalized_keyword:
            return False
        return f" {normalized_keyword} " in f" {normalized_text} "

    @classmethod
    def _matching_keywords(cls, text, keywords):
        return {
            keyword for keyword in keywords
            if cls._text_contains_keyword(text, keyword)
        }

    @staticmethod
    def _normalized_labels(labels):
        return {
            str(label or "").strip().lower().replace(" ", "_")
            for label in (labels or [])
            if str(label or "").strip()
        }

    @classmethod
    def _screen_looks_like_city(cls, labels, ocr_text):
        normalized_labels = cls._normalized_labels(labels)
        if normalized_labels.intersection(CITY_LABEL_HINTS):
            return True
        city_hits = len(cls._matching_keywords(ocr_text, CITY_SCREEN_TEXT_KEYWORDS))
        map_hits = len(cls._matching_keywords(ocr_text, RESOURCE_REVIEW_SCREEN_KEYWORDS))
        return city_hits >= 1 and map_hits == 0

    @classmethod
    def _screen_shows_search_interface(cls, ocr_text):
        resource_hits = cls._matching_keywords(ocr_text, SEARCH_PANEL_RESOURCE_KEYWORDS)
        review_hits = cls._matching_keywords(ocr_text, RESOURCE_REVIEW_SCREEN_KEYWORDS)
        return len(resource_hits) >= 2 or (bool(resource_hits) and bool(review_hits))

    @staticmethod
    def _recent_planner_feedback(context):
        if not context or not hasattr(context, "extracted"):
            return []
        memory_list = context.extracted.get("planner_memory", [])
        if not isinstance(memory_list, list):
            return []
        return [item for item in memory_list if isinstance(item, dict)]

    @classmethod
    def _recent_feedback_contains(cls, context, needle):
        needle_text = str(needle or "").lower()
        return any(
            needle_text in str(item.get("reason", "")).lower()
            for item in cls._recent_planner_feedback(context)
        )

    @staticmethod
    def _last_decision(context):
        if not context or not hasattr(context, "extracted"):
            return {}
        decision = context.extracted.get("planner_last_decision")
        return decision if isinstance(decision, dict) else {}

    @staticmethod
    def _map_toggle_button_center():
        x, y, width, height = UIMap.BOTTOM_RIGHT_MAP_TOGGLE
        return x + (width / 2.0), y + (height / 2.0)

    @classmethod
    def _deterministic_map_transition_decision(cls, context, goal, labels, ocr_text):
        needs_world_map = cls._goal_requests_world_map(goal)
        needs_search_interface = cls._goal_requests_search_interface(goal) or needs_world_map
        if not needs_world_map and not needs_search_interface:
            return None

        normalized_labels = cls._normalized_labels(labels)
        if normalized_labels.intersection(MAP_LABEL_HINTS):
            if needs_search_interface and not cls._screen_shows_search_interface(ocr_text):
                return PlannerDecision.from_mapping(
                    {
                        "thought_process": "The screen no longer looks like city view but the resource-search interface is not open yet.",
                        "action_type": "key",
                        "label": "resource search hotkey",
                        "confidence": 0.99,
                        "delay_seconds": 0.8,
                        "reason": "Open the resource search interface with the guarded search hotkey.",
                        "key_name": "f",
                    }
                )
            return None
        if normalized_labels.intersection(BLOCKER_LABEL_HINTS):
            return PlannerDecision.from_mapping(
                {
                    "thought_process": "A blocker is visible while the current goal is to reach world map.",
                    "action_type": "key",
                    "label": "escape",
                    "confidence": 0.99,
                    "delay_seconds": 0.8,
                    "reason": "Dismiss the visible blocker before opening the world map.",
                    "key_name": "escape",
                }
            )
        last_decision = cls._last_decision(context)
        last_action_type = str(last_decision.get("action_type", "")).lower()
        last_key_name = str(last_decision.get("key_name", "")).lower()
        last_target_id = str(last_decision.get("target_id", "")).lower()

        if not cls._screen_looks_like_city(labels, ocr_text):
            if needs_search_interface and not cls._screen_shows_search_interface(ocr_text):
                search_failed = cls._recent_feedback_contains(
                    context,
                    "search_hotkey_did_not_open_resource_search",
                )
                if last_action_type == "key" and last_key_name == "f" and search_failed:
                    return PlannerDecision.from_mapping(
                        {
                            "thought_process": "The search hotkey did not expose the search interface yet.",
                            "action_type": "wait",
                            "label": "wait",
                            "confidence": 0.99,
                            "delay_seconds": 1.0,
                            "reason": "Re-observe before retrying another search-interface action.",
                        }
                    )
                return PlannerDecision.from_mapping(
                    {
                        "thought_process": "The world map appears reachable but the resource-search interface is not visible yet.",
                        "action_type": "key",
                        "label": "resource search hotkey",
                        "confidence": 0.99,
                        "delay_seconds": 0.8,
                        "reason": "Open the resource search interface with the guarded search hotkey.",
                        "key_name": "f",
                    }
                )
            return None
        map_toggle_failed = cls._recent_feedback_contains(
            context,
            "world_map_toggle_did_not_reach_map_view",
        )
        if (
            (last_action_type == "key" and last_key_name == "space")
            or map_toggle_failed
            or last_target_id == "ui_map_toggle"
        ):
            center_x, center_y = cls._map_toggle_button_center()
            return PlannerDecision.from_mapping(
                {
                    "thought_process": "The world-map hotkey had no visible effect while the screen still looks like city view.",
                    "action_type": "click",
                    "label": "world map button",
                    "confidence": 0.99,
                    "delay_seconds": 1.0,
                    "reason": "Click the fixed map-toggle button because the world-map hotkey did not leave city view.",
                    "target_id": "ui_map_toggle",
                    "x": center_x,
                    "y": center_y,
                },
                source="deterministic",
            )
        return PlannerDecision.from_mapping(
            {
                "thought_process": "The focused goal is to open the world map and the screen still looks like city view.",
                "action_type": "key",
                "label": "world map toggle",
                "confidence": 0.99,
                "delay_seconds": 0.8,
                "reason": "Use the guarded world-map hotkey instead of guessing an OCR target from city view.",
                "key_name": "space",
            }
        )

    def _ocr_review_candidate_score(self, target):
        if not isinstance(target, PlannerTarget) or target.source != "ocr":
            return None

        text = str(target.label or "").strip().lower()
        if not text:
            return None
        
        bounds_arr = self.config.get("OCR_REVIEW_BOUNDS", [0.12, 0.92, 0.10, 0.88])
        if type(bounds_arr) is list and len(bounds_arr) >= 4:
            x_min, x_max, y_min, y_max = map(float, bounds_arr[:4])
        else:
            x_min, x_max, y_min, y_max = 0.12, 0.92, 0.10, 0.88
            
        if target.x < x_min or target.x > x_max or target.y < y_min or target.y > y_max:
            return None
        if any(token in text for token in OCR_UI_BLACKLIST):
            return None
        if ":" in text or "[" in text or "]" in text:
            return None
            
        max_len = int(self.config.get("OCR_REVIEW_MAX_LENGTH", 24))
        if len(text) > max_len:
            return None

        compact_text = re.sub(r"[^a-z0-9]+", "", text)
        digits_only = re.sub(r"\D+", "", text)
        if compact_text.isdigit():
            return None
        if digits_only.isdigit() and len(digits_only) >= 4:
            return None

        score = float(target.confidence)

        resource_bonus = float(self.config.get("OCR_REVIEW_RESOURCE_BONUS", 4.0))
        level_bonus = float(self.config.get("OCR_REVIEW_LEVEL_BONUS", 2.0))
        digit_bonus = float(self.config.get("OCR_REVIEW_DIGIT_BONUS", 3.0))
        min_score = float(self.config.get("OCR_REVIEW_MIN_SCORE", 2.0))
        has_resource_keyword = any(keyword in text for keyword in RESOURCE_TEXT_KEYWORDS)
        has_level_hint = "lv" in text or "level" in text

        if has_resource_keyword:
            score += resource_bonus
        if has_level_hint:
            score += level_bonus
        if (
            digits_only.isdigit()
            and len(digits_only) <= 2
            and 1 <= int(digits_only) <= 8
            and (has_resource_keyword or has_level_hint)
        ):
            score += digit_bonus
        if compact_text.isalnum() and len(compact_text) <= 4 and not compact_text.isdigit():
            score += 0.5

        distance_from_center = abs(target.x - 0.5) + abs(target.y - 0.5)
        score += max(0.0, 0.75 - distance_from_center)
        return score if score >= min_score else None

    def _best_ocr_review_target(self, goal, targets):
        if not self._goal_prefers_resource_targets(goal):
            return None

        best_target = None
        best_score = -1.0
        for target in targets or []:
            score = self._ocr_review_candidate_score(target)
            if score is None:
                continue
            if score > best_score:
                best_target = target
                best_score = score
        return best_target

    @staticmethod
    def _screen_supports_resource_review(ocr_text: str) -> bool:
        """Return whether the current OCR text looks like a resource/map screen."""

        normalized_text = DynamicPlanner._normalized_text(ocr_text)
        resource_hits = DynamicPlanner._matching_keywords(normalized_text, RESOURCE_TEXT_KEYWORDS)
        if DynamicPlanner._matching_keywords(normalized_text, RESOURCE_REVIEW_SCREEN_KEYWORDS):
            return True
        if (" lv " in f" {normalized_text} " or " level " in f" {normalized_text} ") and resource_hits:
            return True
        return len(resource_hits) >= 2

    @staticmethod
    def remember_planner_feedback(context, decision, reason, *, prefix="REJECTED"):
        """Append one bounded planner-memory feedback item for future prompts."""

        if not context or not hasattr(context, "extracted"):
            return

        if isinstance(decision, PlannerDecision):
            decision_detail = decision.to_dict()
        elif isinstance(decision, dict):
            decision_detail = dict(decision)
        else:
            decision_detail = {}

        feedback_reason = f"{prefix}: {reason}"
        signature = "|".join(
            (
                str(decision_detail.get("action_type", "")),
                str(decision_detail.get("target_id", "")),
                str(decision_detail.get("label", "")),
                feedback_reason,
            )
        )
        if context.extracted.get("planner_memory_last_feedback") == signature:
            return

        decision_detail["reason"] = feedback_reason
        memory_list = context.extracted.get("planner_memory", [])
        if not isinstance(memory_list, list):
            memory_list = []
        memory_list.append(decision_detail)
        context.extracted["planner_memory"] = memory_list[-PLANNER_MEMORY_LIMIT:]
        context.extracted["planner_memory_last_feedback"] = signature

    def _ocr_only_review_decision(self, context, goal, detections, targets, decision, ocr_text=""):
        if self._autonomy_level(context) != 1:
            return None
        if detections:
            return None
        if isinstance(decision, PlannerDecision) and decision.action_type not in {"wait", "stop"}:
            return None
        if not self._screen_supports_resource_review(ocr_text):
            return None

        target = self._best_ocr_review_target(goal, targets)
        if target is None:
            return None

        review_confidence = max(
            self._l1_review_min_confidence(),
            min(MIN_PLANNER_CONFIDENCE - 0.01, max(target.confidence, 0.35)),
        )
        return PlannerDecision(
            thought_process="No detector boxes are available; escalate an OCR-only candidate for guarded review.",
            action_type="click",
            label=target.label,
            x=target.x,
            y=target.y,
            confidence=review_confidence,
            reason="YOLO is unavailable on this screen. Use Fix to confirm the resource node before execution.",
            source="ai_review",
            target_id=target.target_id,
            delay_seconds=1.0,
        )

    def _is_l1_reviewable_pointer_decision(self, context, decision, rejection_reason):
        if self._autonomy_level(context) != 1:
            return False
        verdict = decision_verdict(
            decision,
            l1_review_min_confidence=self._l1_review_min_confidence(),
        )
        return verdict.requires_manual_fix and verdict.rejection_reason == str(rejection_reason)

    @staticmethod
    def _record_planner_rejection(context, decision, reason):
        DynamicPlanner.remember_planner_feedback(context, decision, reason)

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
    def request_interrupted(context):
        """Return whether the active runtime paused or stopped a planner request."""

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
        return self._transport.request(request_payload, lambda: self.request_interrupted(context))

    @property
    def transport(self) -> PlannerTransport | None:
        """Expose the planner transport for shared runtime services."""

        return self._transport

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
                      screen_changed=True, planner_memory=None, session_summary=None,
                      teaching_brief=""):
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
            planner_memory: Recent conversational memory of decisions.
            session_summary: Summary of the current session.
            teaching_brief: Optional operator-authored gameplay doctrine for
                teaching mode.
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
            f"\nOCR text: {re.sub(r'\\s+', ' ', str(ocr_text or '')).strip()[:500]}",
        ]

        if resource_context:
            parts.append(f"\nResource status: {json.dumps(resource_context, ensure_ascii=True)}")

        if not screen_changed:
            parts.append("\nNote: The screen has NOT changed since the last action.")

        if stuck_warning:
            parts.append(f"\n{stuck_warning}")

        if session_summary:
            parts.append(f"\nMission Briefing:\n"
                         f"- Duration: {session_summary.get('duration_text', '0s')}\n"
                         f"- Actions Taken: {session_summary.get('total_actions', 0)}\n"
                         f"- Errors/Rejections: {session_summary.get('errors', 0)}/{session_summary.get('planner_rejections', 0)}")

        if teaching_brief:
            parts.append(f"\nGameplay Teaching Brief:\n{teaching_brief}")

        parts.append(f"\nRecent state history: {json.dumps(history, ensure_ascii=True)}")

        if planner_memory:
            parts.append(f"\nRecent planner conversational memory (DO NOT repeat rejected actions):\n{json.dumps(planner_memory, ensure_ascii=True)}")

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
        session_logger = getattr(context, "session_logger", None)
        session_summary = session_logger.summary() if session_logger and hasattr(session_logger, "summary") else {}
        planner_memory = context.extracted.get("planner_memory", []) if context and hasattr(context, "extracted") else []
        teaching_brief = str(getattr(context, "teaching_brief", "") or "") if context else ""

        prompt = self._build_prompt(
            goal=goal,
            labels=labels,
            target_payload=target_payload,
            ocr_text=ocr_text,
            history=history,
            resource_context=resource_context,
            stuck_warning=stuck_warning,
            screen_changed=screen_changed,
            planner_memory=planner_memory,
            session_summary=session_summary,
            teaching_brief=teaching_brief,
        )

        session_logger = getattr(context, "session_logger", None)
        if session_logger and hasattr(session_logger, "summary"):
            api_calls = session_logger.summary().get("api_calls", 0)
            budget_limit = self.config.get("PLANNER_BUDGET_LIMIT", 200)
            if api_calls >= budget_limit:
                overage = api_calls - budget_limit
                delay = min(30.0, 2.0 * overage)
                if overage == 0 or overage % 5 == 0:
                    LOGGER.warning("Planner budget soft limit reached (%d calls). Applying progressive %ds delay.", api_calls, delay)
                time.sleep(delay)

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
                record_stage_timing(context, "planner_request", started_at, detail="cancelled")
                return None
            raw = safe_json_loads(response.output_text)
            llm_decision = PlannerLLMDecision.model_validate(raw)
            decision = self.resolve_target_decision(
                PlannerDecision.from_llm_decision(llm_decision, source="ai"),
                targets,
            )
            record_stage_timing(
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
            record_stage_timing(
                context,
                "planner_request",
                started_at,
                detail=f"error={type(exc).__name__}",
            )
            session_logger = getattr(context, "session_logger", None)
            if session_logger and hasattr(session_logger, "record_error"):
                session_logger.record_error(
                    f"Dynamic planner request failed: {exc}",
                    stage="planner_request",
                    action_type="planner_request",
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
        deterministic_map_decision = self._deterministic_map_transition_decision(context, goal, labels, ocr_text)
        if deterministic_map_decision is not None:
            LOGGER.info(
                "Dynamic planner used deterministic map/search transition: action=%s key=%s target_id=%s goal=%s",
                deterministic_map_decision.action_type,
                deterministic_map_decision.key_name,
                deterministic_map_decision.target_id,
                goal,
            )
            return deterministic_map_decision
        memory_started_at = time.perf_counter()
        try:
            memory_entry = self.memory.find(screenshot_path, labels)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            record_stage_timing(
                context,
                "planner_memory_lookup",
                memory_started_at,
                detail=f"error={type(exc).__name__}",
            )
            LOGGER.warning("Dynamic planner memory lookup failed: %s", exc)
            memory_entry = None
        else:
            record_stage_timing(
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
        fallback_review = self._ocr_only_review_decision(
            context,
            goal,
            detections,
            targets,
            decision,
            ocr_text=ocr_text,
        )
        if fallback_review is not None:
            LOGGER.warning(
                "Dynamic planner surfaced OCR-only L1 Fix review: prior_action=%s target_id=%s label=%s confidence=%s",
                decision.action_type if isinstance(decision, PlannerDecision) else "",
                fallback_review.target_id,
                fallback_review.label,
                fallback_review.confidence,
            )
            return fallback_review
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
