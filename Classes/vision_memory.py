import json
from datetime import datetime
from pathlib import Path

import numpy as np
from logging_config import get_logger
from PIL import Image

LOGGER = get_logger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VISION_MEMORY_PATH = PROJECT_ROOT / "data" / "vision_memory.json"


class VisionMemory:
    """Local visual memory for repeated planner screens.

    The planner can avoid repeated OpenAI calls when it has seen a similar
    screen before. This class stores CLIP image embeddings in JSON metadata and
    uses FAISS for fast similarity search when FAISS is installed. If FAISS is
    unavailable, it falls back to a small NumPy dot-product search.
    """

    def __init__(self, path=DEFAULT_VISION_MEMORY_PATH, similarity_threshold=0.86):
        """Initialize a memory store and load existing entries.

        Args:
            path: JSON file used for persistent memory entries.
            similarity_threshold: Minimum cosine similarity needed for a match.
        """
        self.path = Path(path)
        self.similarity_threshold = float(similarity_threshold)
        self.entries = []
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
            self.entries = []
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("entries", raw if isinstance(raw, list) else [])
            self.entries = [entry for entry in entries if isinstance(entry, dict)]
        except Exception as exc:
            LOGGER.warning(f"Vision memory ignored: {exc}")
            self.entries = []
        return self

    def save(self):
        """Persist memory entries to disk as JSON."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self.entries}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

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

    def find(self, screenshot_or_embedding, visible_labels=None):
        """Find the most similar successful memory entry.

        Args:
            screenshot_or_embedding: Screenshot path or precomputed embedding.
            visible_labels: Optional labels used to narrow candidates.

        Returns:
            dict | None: Best matching entry with a `similarity` field, or None
            when no safe match is found.
        """
        query = self.embed(screenshot_or_embedding)
        if query is None or not self.entries:
            return None

        labels = set(self._labels(visible_labels))
        candidates = []
        embeddings = []
        for entry in self.entries:
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
            distances, indexes = index.search(np.asarray([query], dtype="float32"), 1)
            score = float(distances[0][0])
            candidate = candidates[int(indexes[0][0])]
        else:
            scores = [float(np.dot(query, embedding)) for embedding in embeddings]
            best_index = int(np.argmax(scores))
            score = scores[best_index]
            candidate = candidates[best_index]

        if score < self.similarity_threshold:
            return None
        if int(candidate.get("failure_count", 0)) > int(candidate.get("success_count", 0)) + 2:
            return None
        result = dict(candidate)
        result["similarity"] = score
        return result

    def _record(self, screenshot_path, decision, visible_labels=None, source="ai", corrected=False):
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
            "success_count": 1,
            "failure_count": 0,
            "source": source,
            "corrected": bool(corrected),
            "last_used": now,
        }
        self.entries.append(entry)
        self.save()
        return entry

    def record_success(self, screenshot_path, decision, visible_labels=None, source="ai"):
        """Store a successful AI or memory decision.

        Args:
            screenshot_path: Screenshot path for the current screen.
            decision: PlannerDecision or dictionary.
            visible_labels: Optional detector labels.
            source: Source label for the decision.

        Returns:
            dict | None: Stored memory entry.
        """
        entry = self._record(screenshot_path, decision, visible_labels=visible_labels, source=source, corrected=False)
        if entry:
            LOGGER.info(f"Vision memory success recorded: {entry.get('label')}")
        return entry

    def record_correction(self, screenshot_path, decision, corrected_point, visible_labels=None):
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
        corrected = dict(decision)
        corrected["x"] = float(corrected_point["x"])
        corrected["y"] = float(corrected_point["y"])
        corrected["confidence"] = 1.0
        entry = self._record(screenshot_path, corrected, visible_labels=visible_labels, source="manual", corrected=True)
        if entry:
            LOGGER.info(f"Vision memory correction recorded: {entry.get('label')}")
        return entry

    def record_failure(self, entry_or_decision):
        """Increment a failure counter for a memory entry.

        Args:
            entry_or_decision: Existing entry or decision dictionary.

        Returns:
            dict | None: Updated entry, when one is available.
        """
        entry = entry_or_decision if isinstance(entry_or_decision, dict) and entry_or_decision in self.entries else None
        if not entry and self.entries:
            entry = self.entries[-1]
        if not entry:
            return None
        entry["failure_count"] = int(entry.get("failure_count", 0)) + 1
        entry["last_used"] = datetime.now().isoformat(timespec="seconds")
        self.save()
        LOGGER.warning("Vision memory failure recorded.")
        return entry

    def is_trusted_label(self, label, min_success=3):
        """Check whether a label has enough clean successes for auto mode.

        Args:
            label: Target label to check.
            min_success: Required successful memories with zero failures.

        Returns:
            bool: True when the label can be trusted by Level 2 autonomy.
        """
        label = str(label or "").lower()
        successes = sum(
            int(entry.get("success_count", 0))
            for entry in self.entries
            if str(entry.get("label", "")).lower() == label and int(entry.get("failure_count", 0)) == 0
        )
        return successes >= int(min_success)
