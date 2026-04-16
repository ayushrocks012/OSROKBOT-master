"""Safely purge legacy gameplay template images from OSROKBOT.

This script deletes:
    - Media/Legacy/
    - Loose *.png files directly under Media/

It never touches:
    - Media/UI/
    - Media/Readme/
    - Files nested under any other Media subdirectory

Run a preview first:
    python cleanup_media.py --dry-run

Delete with confirmation:
    python cleanup_media.py

Delete without an interactive prompt:
    python cleanup_media.py --yes
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from termcolor import colored


PROJECT_ROOT = Path(__file__).resolve().parent
MEDIA_DIR = PROJECT_ROOT / "Media"
LEGACY_DIR = MEDIA_DIR / "Legacy"
PROTECTED_DIRS = {
    (MEDIA_DIR / "UI").resolve(),
    (MEDIA_DIR / "Readme").resolve(),
}


def _is_protected(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        resolved = path.absolute()
    return any(resolved == protected or protected in resolved.parents for protected in PROTECTED_DIRS)


def collect_targets() -> list[Path]:
    """Return the exact legacy media paths this script is allowed to delete."""
    targets: list[Path] = []

    if LEGACY_DIR.exists() and not _is_protected(LEGACY_DIR):
        targets.append(LEGACY_DIR)

    if MEDIA_DIR.exists():
        for path in sorted(MEDIA_DIR.glob("*.png")):
            if path.is_file() and not _is_protected(path):
                targets.append(path)

    return targets


def delete_target(path: Path, dry_run: bool) -> None:
    action = "Would delete" if dry_run else "Deleting"
    print(colored(f"{action}: {path}", "yellow"))
    if dry_run:
        return

    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Purge obsolete OSROKBOT gameplay template media.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting anything.")
    parser.add_argument("--yes", action="store_true", help="Delete without asking for confirmation.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = collect_targets()

    if not targets:
        print(colored("No legacy gameplay media targets found.", "green"))
        return 0

    print(colored("Protected directories will not be touched:", "cyan"))
    for protected in sorted(PROTECTED_DIRS):
        print(colored(f"  - {protected}", "cyan"))

    print(colored("\nTargets:", "yellow"))
    for target in targets:
        print(f"  - {target}")

    if args.dry_run:
        print(colored("\nDry run complete. No files were deleted.", "green"))
        return 0

    if not args.yes:
        answer = input("\nDelete these legacy gameplay media files? Type DELETE to continue: ").strip()
        if answer != "DELETE":
            print(colored("Cleanup cancelled.", "yellow"))
            return 1

    for target in targets:
        delete_target(target, dry_run=False)

    print(colored("Legacy gameplay media cleanup complete.", "green"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
