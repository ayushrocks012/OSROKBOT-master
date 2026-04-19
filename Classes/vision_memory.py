from __future__ import annotations

import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from dynamic_planner import PlannerDecision
from logging_config import get_logger
from PIL import Image

LOGGER = get_logger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VISION_MEMORY_PATH = PROJECT_ROOT / "data" / "vision_memory.json"
DEFAULT_MAX_ENTRIES = 500
DECAY_HALF_LIFE_DAYS = 30.0


class VisionMemory:
    """Local visual memory for repeated planner screens.

    The planner can avoid repeated OpenAI calls when it has seen a similar
    screen before. This class stores CLIP image embeddings in JSON metadata and
    uses FAISS for fast similarity search when FAISS is installed. If FAISS is
    unavailable, it falls back to a small NumPy dot-product search.

    Improvements over the original design:
    - Time-weighted decay: older entries score lower.
    - Max-entries cap with eviction of lowest-value entries.
    - Per-label failure tracking: failures are matched by embedding similarity
      to the actual failing entry, not blindly applied to the most recent one.
    - Label-match bonus: entries whose label matches a currently visible label
      get a similarity boost.
    """

    def __init__(self, path=DEFAULT_VISION_MEMORY_PATH, similarity_threshold=0.86,
                 max_entries=DEFAULT_MAX_ENTRIES):
        """Initialize a memory store and load existing entries.

        Args:
            path: JSON file used for persistent memory entries.
            similarity_threshold: Minimum cosine similarity needed for a match.
            max_entries: Maximum number of entries to keep. Oldest/lowest-value
                entries are evicted when this limit is exceeded.
        """
        self.path = Path(path)
        self.similarity_threshold = float(similarity_threshold)
        self.max_entries = int(max_entries)
        self.entries: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._model = None
        self._model_error = None
        self._faiss = None
        self._index = None
        self.load()

    def load(self):
        """Load memory entries from disk.

        Returns:
            VisionMemory: This instance, for convenient chaining.
        """
        if not self.path.is_file():
            with self._lock:
                self.entries = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("entries", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
            with self._lock:
                self.entries = [entry for entry in entries if isinstance(entry, dict)]
                self._evict_if_needed()
        except Exception as exc:
            LOGGER.warning(f"Vision memory ignored: {exc}")
            with self._lock:
                self.entries = []
        return self

    def save(self):
        """Persist memory entries to disk as JSON, enforcing the max-entries cap."""
        with self._lock:
            self._evict_if_needed()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 2, "entries": self.entries}
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
        """Remove lowest-value entries when the cap is exceeded."""
        if len(self.entries) <= self.max_entries:
            return

        scored = []
        for entry in self.entries:
            freshness = self._freshness_factor(entry)
            success = int(entry.get("success_count", 0))
            failure = int(entry.get("failure_count", 0))
            # Value = freshness-weighted net success score.
            value = freshness * max(0.0, success - failure * 0.5)
            scored.append((value, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        self.entries = [entry for _, entry in scored[:self.max_entries]]
        evicted = len(scored) - len(self.entries)
        if evicted > 0:
            LOGGER.info(f"Vision memory evicted {evicted} low-value entries (cap={self.max_entries}).")

    @staticmethod
    def _freshness_factor(entry):
        """Compute a time-decay factor based on last_used timestamp.

        Returns a value between 0 and 1, where 1 means "just used" and
        values decrease exponentially with a half-life of DECAY_HALF_LIFE_DAYS.
        """
        last_used = entry.get("last_used")
        if not last_used:
            return 0.5  # Unknown age gets a neutral score.
        try:
            then = datetime.fromisoformat(last_used)
            now = datetime.now()
            days_elapsed = max(0.0, (now - then).total_seconds() / 86400.0)
            return math.exp(-0.693 * days_elapsed / DECAY_HALF_LIFE_DAYS)
        except Exception:
            return 0.5

    def _load_model(self):
        """Load the CLIP embedding model lazily.

        Returns:
            SentenceTransformer | None: The CLIP model, or None if the optional
            dependency/model download is unavailable.
        """
        if self._model is not None:
            return self._model
        if self._model_error is not None:
            return None
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer("clip-ViT-B-32")
            return self._model
        except Exception as exc:
            self._model_error = exc
            LOGGER.warning(f"CLIP vision memory unavailable: {exc}")
            return None

    @staticmethod
    def _normalize(vector):
        """Normalize an embedding vector for cosine similarity.

        Args:
            vector: Raw embedding values.

        Returns:
            numpy.ndarray: Float32 vector with length 1 when possible.
        """
        array = np.asarray(vector, dtype="float32")
        norm = np.linalg.norm(array)
        if norm <= 0:
            return array
        return array / norm

    def embed(self, screenshot_or_embedding):
        """Convert a screenshot or existing vector into a normalized embedding.

        Args:
            screenshot_or_embedding: Path to an image, or an existing vector.

        Returns:
            numpy.ndarray | None: Normalized embedding, or None if embedding is
            unavailable.
        """
        if isinstance(screenshot_or_embedding, list | tuple | np.ndarray):
            return self._normalize(screenshot_or_embedding)
        model = self._load_model()
        if not model:
            return None
        try:
            image = Image.open(screenshot_or_embedding).convert("RGB")
            encoded = model.encode([image])
            return self._normalize(encoded[0] if hasattr(encoded, "__len__") else encoded)
        except Exception as exc:
            LOGGER.warning(f"Unable to embed screenshot for vision memory: {exc}")
            return None

    @staticmethod
    def _labels(visible_labels):
        """Normalize detector labels for storage and filtering.

        Args:
            visible_labels: Strings, dictionaries, or Detection-like objects.

        Returns:
            list[str]: Sorted non-empty labels.
        """
        labels = []
        for item in visible_labels or []:
            if isinstance(item, str):
                labels.append(item)
            elif isinstance(item, dict):
                labels.append(str(item.get("label", "")))
            elif hasattr(item, "label"):
                labels.append(str(item.label))
        return sorted(label for label in labels if label)

    def _ensure_faiss_index(self, embeddings):
        """Build a temporary FAISS index for the provided embeddings.

        Args:
            embeddings: Normalized embedding vectors.

        Returns:
            faiss.Index | None: Index when FAISS is installed, otherwise None.
        """
        try:
            import faiss
        except Exception:
            return None
        if not embeddings:
            return None
        matrix = np.asarray(embeddings, dtype="float32")
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        return index

    def find(self, screenshot_or_embedding, visible_labels=None, mission: str = ""):
        """Find the most similar successful memory entry.

        Uses time-weighted decay and label-match bonuses for better ranking.

        Args:
            screenshot_or_embedding: Screenshot path or precomputed embedding.
            visible_labels: Optional labels used to narrow candidates.

        Returns:
            dict | None: Best matching entry with a ``similarity`` field, or None
            when no safe match is found.
        """
        with self._lock:
            entries = list(self.entries)
        if not entries:
            return None

        query = self.embed(screenshot_or_embedding)
        if query is None:
            return None

        labels = set(self._labels(visible_labels))
        candidates = []
        embeddings = []
        for entry in entries:
            embedding = entry.get("embedding")
            if not embedding:
                continue
            entry_labels = set(entry.get("visible_labels", []))
            if labels and entry_labels and not labels.intersection(entry_labels):
                continue
            candidates.append(entry)
            embeddings.append(self._normalize(embedding))

        if not candidates:
            return None

        index = self._ensure_faiss_index(embeddings)
        if index is not None:
            distances, indexes = index.search(np.asarray([query], dtype="float32"), min(5, len(candidates)))
            scored = []
            for rank in range(len(distances[0])):
                raw_score = float(distances[0][rank])
                candidate = candidates[int(indexes[0][rank])]
                scored.append((raw_score, candidate))
        else:
            scores = [float(np.dot(query, embedding)) for embedding in embeddings]
            scored = sorted(zip(scores, candidates, strict=False), key=lambda x: x[0], reverse=True)[:5]

        # Apply time decay and label-match bonus to top candidates.
        best_entry = None
        best_adjusted_score = -1.0
        for raw_score, candidate in scored:
            if raw_score < self.similarity_threshold * 0.9:
                continue

            freshness = self._freshness_factor(candidate)
            # Label-match bonus: +5% if the entry label matches a visible label.
            label_bonus = 0.0
            entry_label = str(candidate.get("label", "")).lower()
            if labels and entry_label and any(
                label.lower() == entry_label for label in labels
            ):
                label_bonus = 0.05
            
            # Mission-match bonus: +8% if the active mission overlaps with memory context
            mission_bonus = 0.0
            if mission:
                query_words = set(w.lower() for w in str(mission).split() if len(w) >= 3)
                entry_kws = set(candidate.get("mission_keywords", []))
                if query_words and entry_kws and query_words.intersection(entry_kws):
                     mission_bonus = 0.08

            adjusted_score = raw_score * freshness + label_bonus + mission_bonus

            # Failure gate: per-label check.
            if int(candidate.get("failure_count", 0)) > int(candidate.get("success_count", 0)) + 2:
                continue

            if adjusted_score > best_adjusted_score:
                best_adjusted_score = adjusted_score
                best_entry = candidate

        if best_entry is None or best_adjusted_score < self.similarity_threshold:
            return None

        result = dict(best_entry)
        result["similarity"] = best_adjusted_score
        return result

    def _find_matching_entry(self, decision_or_embedding):
        """Find the entry that best matches a failed decision by embedding similarity.

        This prevents the old bug of blindly penalizing entries[-1].

        Args:
            decision_or_embedding: A decision dict, embedding vector, or entry.

        Returns:
            dict | None: The matching entry from self.entries, or None.
        """
        # If it *is* one of our entries, return it directly.
        with self._lock:
            entries = list(self.entries)

        if isinstance(decision_or_embedding, dict) and decision_or_embedding in entries:
            return decision_or_embedding

        # Try to find by embedding similarity.
        embedding = None
        if isinstance(decision_or_embedding, dict):
            embedding = decision_or_embedding.get("embedding")
            if embedding:
                embedding = self._normalize(embedding)

        if embedding is not None and entries:
            best_entry = None
            best_score = -1.0
            for entry in entries:
                entry_embedding = entry.get("embedding")
                if not entry_embedding:
                    continue
                score = float(np.dot(embedding, self._normalize(entry_embedding)))
                if score > best_score:
                    best_score = score
                    best_entry = entry
            if best_entry is not None and best_score > 0.8:
                return best_entry

        # Try to match by label+target_id if available.
        if isinstance(decision_or_embedding, dict):
            label = str(decision_or_embedding.get("label", "")).lower()
            target_id = decision_or_embedding.get("target_id", "")
            if label or target_id:
                for entry in reversed(entries):
                    entry_label = str(entry.get("label", "")).lower()
                    entry_target = entry.get("target_id", "")
                    if (label and entry_label == label) or (target_id and entry_target == target_id):
                        return entry

        return None

    @staticmethod
    def _point_distance(left, right):
        left = left or {}
        right = right or {}
        try:
            return abs(float(left.get("x", 0.0)) - float(right.get("x", 0.0))) + abs(
                float(left.get("y", 0.0)) - float(right.get("y", 0.0))
            )
        except Exception:
            return math.inf

    def _merge_or_append(self, entry):
        """Merge equivalent entries to prevent unbounded duplicate growth."""
        for existing in reversed(self.entries):
            if str(existing.get("label", "")).lower() != str(entry.get("label", "")).lower():
                continue
            if existing.get("action_type", "click") != entry.get("action_type", "click"):
                continue
            if bool(existing.get("corrected", False)) != bool(entry.get("corrected", False)):
                continue
            existing_target = existing.get("target_id", "")
            new_target = entry.get("target_id", "")
            if existing_target and new_target and existing_target != new_target:
                continue
            if self._point_distance(existing.get("normalized_point"), entry.get("normalized_point")) > 0.02:
                continue

            existing["embedding"] = entry["embedding"]
            existing["visible_labels"] = sorted(set(existing.get("visible_labels", [])) | set(entry.get("visible_labels", [])))
            existing["mission_keywords"] = sorted(set(existing.get("mission_keywords", [])) | set(entry.get("mission_keywords", [])))
            existing["normalized_point"] = entry["normalized_point"]
            existing["confidence"] = max(float(existing.get("confidence", 0.0)), float(entry.get("confidence", 0.0)))
            existing["success_count"] = int(existing.get("success_count", 0)) + int(entry.get("success_count", 1))
            existing["source"] = entry.get("source", existing.get("source", "ai"))
            existing["last_used"] = entry["last_used"]
            return existing

        self.entries.append(entry)
        return entry

    def _record(self, screenshot_path, decision, visible_labels=None, source="ai", corrected=False, mission: str = ""):
        """Record a successful decision and its visual embedding.

        Args:
            screenshot_path: Screenshot used to produce the decision.
            decision: PlannerDecision or equivalent dictionary.
            visible_labels: Optional detector labels.
            source: `ai`, `memory`, or `manual`.
            corrected: True when the point came from human correction.

        Returns:
            dict | None: Stored entry, or None if embedding failed.
        """
        embedding = self.embed(screenshot_path)
        if embedding is None:
            return None
        now = datetime.now().isoformat(timespec="seconds")
        if hasattr(decision, "to_dict"):
            decision = decision.to_dict()
        entry = {
            "embedding": embedding.tolist(),
            "visible_labels": self._labels(visible_labels),
            "label": decision.get("label", ""),
            "normalized_point": {"x": float(decision.get("x", 0.0)), "y": float(decision.get("y", 0.0))},
            "confidence": float(decision.get("confidence", 0.0)),
            "action_type": decision.get("action_type", "click"),
            "target_id": decision.get("target_id", ""),
            "delay_seconds": float(decision.get("delay_seconds", 1.0)),
            "success_count": 1,
            "failure_count": 0,
            "source": source,
            "corrected": bool(corrected),
            "last_used": now,
            "mission_keywords": sorted(set(w.lower() for w in str(mission or "").split() if len(w) >= 3)),
        }
        with self._lock:
            stored_entry = self._merge_or_append(entry)
            self.save()
            return stored_entry

    def record_success(self, screenshot_path: str | Path, decision: PlannerDecision | dict[str, Any], visible_labels: list[Any] | None = None, source: str = "ai", mission: str = "") -> dict[str, Any] | None:
        """Store a successful AI or memory decision.

        Args:
            screenshot_path: Screenshot path for the current screen.
            decision: PlannerDecision or dictionary.
            visible_labels: Optional detector labels.
            source: Source label for the decision.

        Returns:
            dict | None: Stored memory entry.
        """
        entry = self._record(screenshot_path, decision, visible_labels=visible_labels, source=source, corrected=False, mission=mission)
        if entry:
            LOGGER.info(f"Vision memory success recorded: {entry.get('label')}")
        return entry

    def record_correction(self, screenshot_path: str | Path, decision: PlannerDecision | dict[str, Any], corrected_point: dict[str, Any], visible_labels: list[Any] | None = None, mission: str = "") -> dict[str, Any] | None:
        """Store a human-corrected target point.

        Args:
            screenshot_path: Screenshot path for the current screen.
            decision: Original planner decision.
            corrected_point: Normalized point with `x` and `y`.
            visible_labels: Optional detector labels.

        Returns:
            dict | None: Stored correction entry.
        """
        if hasattr(decision, "to_dict"):
            decision = decision.to_dict()
        decision_dict = dict(decision)
        decision_dict["x"] = float(corrected_point["x"])
        decision_dict["y"] = float(corrected_point["y"])
        decision_dict["confidence"] = 1.0
        entry = self._record(screenshot_path, decision_dict, visible_labels=visible_labels, source="manual", corrected=True, mission=mission)
        if entry:
            LOGGER.info(f"Vision memory correction recorded: {entry.get('label')}")
        return entry

    def record_failure(self, entry_or_decision: PlannerDecision | dict[str, Any]) -> dict[str, Any] | None:
        """Increment a failure counter for the matching memory entry.

        Fixed: Now finds the actual matching entry by embedding similarity
        or label+target_id, instead of blindly penalizing the last entry.

        Args:
            entry_or_decision: Existing entry or decision dictionary.

        Returns:
            dict | None: Updated entry, when one is available.
        """
        with self._lock:
            entry = self._find_matching_entry(entry_or_decision)
            if not entry:
                LOGGER.warning("Vision memory failure: no matching entry found to penalize.")
                return None
            entry["failure_count"] = int(entry.get("failure_count", 0)) + 1
            entry["last_used"] = datetime.now().isoformat(timespec="seconds")
            self.save()
        LOGGER.warning(f"Vision memory failure recorded for label: {entry.get('label', 'unknown')}")
        return entry

    def is_trusted_label(self, label: str, min_success: int = 3) -> bool:
        """Check whether a label has enough clean successes for auto mode.

        Args:
            label: Target label to check.
            min_success: Required successful memories with zero failures.

        Returns:
            bool: True when the label can be trusted by Level 2 autonomy.
        """
        label = str(label or "").lower()
        with self._lock:
            successes = sum(
                int(entry.get("success_count", 0))
                for entry in self.entries
                if str(entry.get("label", "")).lower() == label and int(entry.get("failure_count", 0)) == 0
            )
        return successes >= int(min_success)
