"""Static integrity checks for OSROKBOT workflows.

Run from the project root:
    python verify_integrity.py

The script validates the workflow image references, state-machine transitions,
and local OCR executable path without starting the bot or clicking the game UI.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
CLASSES_DIR = PROJECT_ROOT / "Classes"
ACTION_SETS_PATH = CLASSES_DIR / "action_sets.py"
ENV_PATH = PROJECT_ROOT / ".env"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


def _normalize_media_path(value: str) -> Path | None:
    normalized = value.replace("\\", "/")
    if not normalized.lower().endswith(IMAGE_SUFFIXES):
        return None
    if not normalized.startswith("Media/"):
        return None
    return PROJECT_ROOT / normalized


def collect_action_set_image_paths() -> list[Path]:
    tree = ast.parse(ACTION_SETS_PATH.read_text(encoding="utf-8"))
    image_paths: list[Path] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            image_path = _normalize_media_path(node.value)
            if image_path:
                image_paths.append(image_path)
    return sorted(set(image_paths))


def check_action_set_images() -> list[str]:
    failures: list[str] = []
    for image_path in collect_action_set_image_paths():
        if not image_path.is_file():
            failures.append(f"Missing image referenced by action_sets.py: {image_path}")
    return failures


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _resolve_helpers_get_random_rss() -> set[str]:
    helpers_path = CLASSES_DIR / "helpers.py"
    tree = ast.parse(helpers_path.read_text(encoding="utf-8"))
    states: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "choice":
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            continue
        for item in node.args[0].elts:
            value = _literal_string(item)
            if value:
                states.add(value)
    return states


def _dynamic_targets(node: ast.AST | None) -> set[str]:
    if not isinstance(node, ast.Call):
        return set()
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "getRandomRss"
        and isinstance(func.value, ast.Name)
        and func.value.id == "Helpers"
    ):
        return _resolve_helpers_get_random_rss()
    return set()


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _iter_action_set_methods() -> list[ast.FunctionDef]:
    tree = ast.parse(ACTION_SETS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ActionSets":
            return [
                child for child in node.body
                if isinstance(child, ast.FunctionDef)
                and not child.name.startswith("_")
                and child.name not in {"create_machine", "map_view_precondition"}
            ]
    return []


def check_state_machine_transitions() -> list[str]:
    failures: list[str] = []
    for method in _iter_action_set_methods():
        state_names: set[str] = set()
        transitions: list[tuple[str, str, str]] = []
        initial_state: str | None = None

        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue

            call_name = _call_name(node)
            if call_name == "add_state":
                state_name = _literal_string(node.args[0]) if node.args else None
                if not state_name:
                    failures.append(f"{method.name}: add_state call is missing a literal state name")
                    continue

                state_names.add(state_name)
                positional_targets = {
                    "success": node.args[2] if len(node.args) > 2 else None,
                    "failure": node.args[3] if len(node.args) > 3 else None,
                }
                keyword_targets = {
                    keyword.arg: keyword.value
                    for keyword in node.keywords
                    if keyword.arg in {"next_state_on_success", "next_state_on_failure", "fallback_state"}
                }

                for label, target_node in {**positional_targets, **keyword_targets}.items():
                    literal = _literal_string(target_node)
                    if literal:
                        transitions.append((state_name, label, literal))
                        continue
                    for dynamic_target in _dynamic_targets(target_node):
                        transitions.append((state_name, label, dynamic_target))

            elif call_name == "set_initial_state":
                initial_state = _literal_string(node.args[0]) if node.args else None

        if not state_names:
            continue

        if initial_state not in state_names:
            failures.append(f"{method.name}: initial state does not exist: {initial_state}")

        for state_name, label, target in transitions:
            if target not in state_names:
                failures.append(
                    f"{method.name}.{state_name}: {label} transition points to unknown state {target!r}"
                )

    return failures


def _read_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.is_file():
        return values

    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def check_tesseract_path() -> list[str]:
    values = _read_env_values()
    tesseract_path = values.get("TESSERACT_PATH")
    if not tesseract_path:
        return ["TESSERACT_PATH is missing from .env"]

    resolved = Path(os.path.expandvars(tesseract_path))
    if not resolved.is_file():
        return [f"TESSERACT_PATH is not accessible: {resolved}"]
    return []


def check_ui_map_coordinates() -> list[str]:
    failures: list[str] = []
    if str(CLASSES_DIR) not in sys.path:
        sys.path.insert(0, str(CLASSES_DIR))

    try:
        from helpers import UIMap
    except Exception as exc:
        return [f"Unable to import UIMap from Classes/helpers.py: {exc}"]

    for name, value in vars(UIMap).items():
        if name.startswith("_") or not name.isupper():
            continue
        if not isinstance(value, tuple):
            failures.append(f"UIMap.{name} must be a tuple, got {type(value).__name__}")
            continue
        if len(value) != 4:
            failures.append(f"UIMap.{name} must contain 4 values: (x, y, width, height)")
            continue
        if not all(isinstance(item, (int, float)) for item in value):
            failures.append(f"UIMap.{name} contains non-numeric values: {value!r}")
            continue

        x, y, width, height = value
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            failures.append(f"UIMap.{name} has invalid origin or size: {value!r}")
            continue
        if x > 1 or y > 1 or width > 1 or height > 1:
            failures.append(f"UIMap.{name} values must be normalized between 0.0 and 1.0: {value!r}")
            continue
        if x + width > 1.0 or y + height > 1.0:
            failures.append(f"UIMap.{name} extends outside normalized screen bounds: {value!r}")

    return failures


def main() -> int:
    checks = {
        "action set image paths": check_action_set_images,
        "state-machine transitions": check_state_machine_transitions,
        "TESSERACT_PATH": check_tesseract_path,
        "UIMap coordinates": check_ui_map_coordinates,
    }

    failures: list[str] = []
    for label, check in checks.items():
        try:
            check_failures = check()
        except Exception as exc:
            check_failures = [f"{label} check crashed: {exc}"]

        if check_failures:
            print(f"[FAIL] {label}")
            for failure in check_failures:
                print(f"  - {failure}")
            failures.extend(check_failures)
        else:
            print(f"[OK] {label}")

    if failures:
        print(f"\nIntegrity check failed with {len(failures)} issue(s).")
        return 1

    print("\nIntegrity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
