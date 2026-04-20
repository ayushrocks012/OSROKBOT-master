"""Fail on tracked runtime artifacts and unsupported import paths.

This script is intentionally narrow. It checks for tracked generated files that
should stay ignored and for production code importing the removed legacy action
surface.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_TRACKED_PATTERNS = (
    ".artifacts/**",
    "data/handoff/**",
    "data/logs/**",
    "data/secrets/**",
    "data/session_logs/**",
)
SOURCE_SUFFIXES = {".py"}


def _git_ls_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    raw_paths = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [Path(item) for item in raw_paths if item]


def _tracked_artifact_failures(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        normalized = path.as_posix()
        if not (PROJECT_ROOT / path).exists():
            continue
        if any(fnmatch.fnmatch(normalized, pattern) for pattern in FORBIDDEN_TRACKED_PATTERNS):
            failures.append(f"Tracked generated artifact must be removed: {normalized}")
    return failures


def _legacy_import_failures(paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        if path.suffix not in SOURCE_SUFFIXES:
            continue
        absolute_path = PROJECT_ROOT / path
        if not absolute_path.exists():
            continue
        text = absolute_path.read_text(encoding="utf-8", errors="ignore")
        if "Actions.legacy" in text:
            failures.append(f"Unsupported legacy import reference found: {path.as_posix()}")
    return failures


def main() -> int:
    try:
        tracked_paths = _git_ls_files()
    except subprocess.CalledProcessError as exc:
        print(f"[FAIL] unable to list tracked files: {exc}", file=sys.stderr)
        return 1

    failures = _tracked_artifact_failures(tracked_paths)
    failures.extend(_legacy_import_failures(tracked_paths))
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1
    print("[OK] repository hygiene")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
