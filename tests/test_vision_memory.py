from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from dynamic_planner import PlannerDecision
from recovery_memory import RecoveryMemory
from vision_memory import VisionMemory


def test_vision_memory_finds_similar_entry_without_faiss(tmp_path):
    memory = VisionMemory(path=tmp_path / "memory.json", similarity_threshold=0.5)
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
            "last_used": datetime.now().isoformat(timespec="seconds"),
        }
    ]

    found = memory.find([1.0, 0.0, 0.0], visible_labels=["gather"])

    assert found is not None
    assert found["label"] == "gather"


def test_vision_memory_find_skips_embedding_when_no_entries(tmp_path):
    memory = VisionMemory(path=tmp_path / "memory.json")
    memory.embed = lambda _value: (_ for _ in ()).throw(AssertionError("embed should not load"))  # noqa: E731

    assert memory.find(tmp_path / "screen.png") is None


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


def test_vision_memory_merges_equivalent_successes(tmp_path):
    memory = VisionMemory(path=tmp_path / "memory.json")
    memory.embed = lambda value: np.asarray([1.0, 0.0, 0.0], dtype="float32")  # noqa: E731
    decision = PlannerDecision("t", "click", "gather", 0.5, 0.5, 0.9, "Gather.", target_id="det_1")

    first = memory.record_success(tmp_path / "screen.png", decision, visible_labels=["gather"])
    second = memory.record_success(tmp_path / "screen.png", decision, visible_labels=["button"])

    assert first is second
    assert len(memory.entries) == 1
    assert memory.entries[0]["success_count"] == 2
    assert memory.entries[0]["visible_labels"] == ["button", "gather"]


def test_recovery_memory_save_uses_atomic_replace_and_retention(tmp_path):
    memory = RecoveryMemory(path=tmp_path / "recovery.json", max_entries=1)
    memory.entries = {
        "old": {"signature": "old", "success_count": 1, "failure_count": 3, "last_used": "2024-01-01T00:00:00"},
        "new": {"signature": "new", "success_count": 3, "failure_count": 0, "last_used": "2026-01-01T00:00:00"},
    }
    memory.save()

    loaded = RecoveryMemory.load(tmp_path / "recovery.json", max_entries=1)

    assert len(loaded.entries) == 1
    assert "new" in loaded.entries
    assert not (tmp_path / "recovery.json.tmp").exists()
