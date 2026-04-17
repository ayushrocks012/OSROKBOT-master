"""Retention helpers for generated runtime artifacts.

This module keeps local diagnostics, session logs, and recovery dataset exports
bounded by count and age. It operates on file groups keyed by stem so related
artifacts such as `.png`, `.log`, `.meta`, and `.point` files are deleted
together.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from logging_config import get_logger

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class ArtifactRetentionPolicy:
    """One retention policy for a generated-artifact directory."""

    max_groups: int
    max_age_days: float | None = None


def policy_from_environment(
    *,
    max_groups_env: str,
    max_age_days_env: str,
    default_max_groups: int,
    default_max_age_days: float | None = None,
) -> ArtifactRetentionPolicy:
    """Build a retention policy from environment overrides and defaults."""

    max_groups = default_max_groups
    max_age_days = default_max_age_days
    raw_max_groups = os.getenv(max_groups_env)
    raw_max_age_days = os.getenv(max_age_days_env)

    if raw_max_groups:
        try:
            max_groups = max(1, int(raw_max_groups))
        except ValueError:
            LOGGER.warning("Invalid artifact retention count for %s: %s", max_groups_env, raw_max_groups)
    if raw_max_age_days:
        try:
            max_age_days = max(0.0, float(raw_max_age_days))
        except ValueError:
            LOGGER.warning("Invalid artifact retention age for %s: %s", max_age_days_env, raw_max_age_days)

    return ArtifactRetentionPolicy(max_groups=max_groups, max_age_days=max_age_days)


class ArtifactRetentionManager:
    """Apply grouped retention policies to generated runtime artifacts."""

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.time

    def prune_directory(
        self,
        directory: str | Path,
        policy: ArtifactRetentionPolicy,
        *,
        group_key: Callable[[Path], str] | None = None,
    ) -> list[Path]:
        """Delete stale or excess artifact groups and return removed files."""

        target_directory = Path(directory)
        if not target_directory.is_dir():
            return []

        key_for_path = group_key or (lambda path: path.stem)
        grouped_files: dict[str, list[Path]] = {}
        group_mtime: dict[str, float] = {}
        for path in target_directory.iterdir():
            if not path.is_file():
                continue
            key = key_for_path(path)
            grouped_files.setdefault(key, []).append(path)
            modified_at = path.stat().st_mtime
            group_mtime[key] = max(group_mtime.get(key, 0.0), modified_at)

        if not grouped_files:
            return []

        cutoff = None
        if policy.max_age_days is not None:
            cutoff = self._now() - policy.max_age_days * 86400.0

        sorted_groups = sorted(grouped_files, key=lambda key: group_mtime[key], reverse=True)
        retained_count = 0
        removed_files: list[Path] = []
        for key in sorted_groups:
            newest_mtime = group_mtime[key]
            group_is_stale = cutoff is not None and newest_mtime < cutoff
            if not group_is_stale and retained_count < policy.max_groups:
                retained_count += 1
                continue

            for path in grouped_files[key]:
                try:
                    path.unlink(missing_ok=True)
                    removed_files.append(path)
                except OSError as exc:
                    LOGGER.warning("Artifact retention failed for %s: %s", path, exc)

        if removed_files:
            LOGGER.info("Artifact retention pruned %s files in %s", len(removed_files), target_directory)
        return removed_files
