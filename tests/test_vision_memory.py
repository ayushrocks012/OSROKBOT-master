from pathlib import Path

import numpy as np
import pytest

from dynamic_planner import PlannerDecision
from vision_memory import VisionMemory


def test_vision_memory_finds_similar_entry_without_faiss(tmp_path):
    memory = VisionMemory(path=tmp_path / "memory.json", similarity_threshold=0.8)
    memory.embed = lambda value: np.asarray(value, dtype="float32")  # noqa: E731
    memory._ensure_faiss_index = lambda embeddings: None  # noqa: E731
    memory.entries = [
        {
            "embedding": [1.0, 0.0, 0.0],
            "visible_labels": ["gather"],
            "label": "gather",
            "normalized_point": {"x": 0.4, "y": 0.6},
            "confidence": 0.92,
            "action_type": "click",
            "success_count": 3,
            "failure_count": 0,
        }
    ]

    found = memory.find([1.0, 0.0, 0.0], visible_labels=["gather"])

    assert found is not None
    assert found["label"] == "gather"
    assert found["similarity"] == 1.0


def test_vision_memory_records_correction_with_manual_source(tmp_path):
    memory = VisionMemory(path=tmp_path / "memory.json")
    memory.embed = lambda value: np.asarray([1.0, 0.0, 0.0], dtype="float32")  # noqa: E731
    decision = PlannerDecision("t", "click", "wrong", 0.1, 0.2, 0.5, "Wrong target.")

    entry = memory.record_correction(
        tmp_path / "screen.png",
        decision,
        corrected_point={"x": 0.7, "y": 0.8},
        visible_labels=["gather"],
    )

    assert entry["source"] == "manual"
    assert entry["corrected"] is True
    assert entry["normalized_point"] == {"x": 0.7, "y": 0.8}


def test_vision_memory_save_uses_atomic_replace(tmp_path, monkeypatch):
    memory_path = tmp_path / "memory.json"
    original = '{"version": 1, "entries": [{"label": "old"}]}\n'
    memory_path.write_text(original, encoding="utf-8")
    memory = VisionMemory(path=memory_path)
    memory.entries = [{"label": "new"}]
    replace_calls = []

    def fail_replace(self, target):
        replace_calls.append((self, target))
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError):
        memory.save()

    assert replace_calls == [(memory_path.with_suffix(".json.tmp"), memory_path)]
    assert memory_path.read_text(encoding="utf-8") == original
    assert memory_path.with_suffix(".json.tmp").is_file()
