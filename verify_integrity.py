"""Static and runtime integrity checks for OSROKBOT workflows.

Run from the project root:
    python verify_integrity.py

The script validates workflow image references, state-machine transitions,
normalized UI regions, and pre-flight runtime health without starting the bot
or clicking the game UI.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CLASSES_DIR = PROJECT_ROOT / "Classes"
ACTION_SETS_PATH = CLASSES_DIR / "action_sets.py"
ENV_PATH = PROJECT_ROOT / ".env"
CONFIG_PATH = PROJECT_ROOT / "config.json"
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")

if str(CLASSES_DIR) not in sys.path:
    sys.path.insert(0, str(CLASSES_DIR))

from logging_config import get_logger

LOGGER = get_logger(Path(__file__).stem)
DEPENDENCY_ENV_REQUIREMENTS = {
    "openai": (("OPENAI_KEY", "OPENAI_API_KEY"),),
    "pytesseract": (("TESSERACT_PATH",),),
}
ENV_VAR_PATTERN = re.compile(r"%([^%]+)%|\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _expand_env_vars(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        key = next(group for group in match.groups() if group)
        return os.environ.get(key, match.group(0))

    return ENV_VAR_PATTERN.sub(replace, value)


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


def _read_config_values() -> dict[str, str]:
    values = _read_env_values()
    if not CONFIG_PATH.is_file():
        return values
    try:
        config_values = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return values
    if not isinstance(config_values, dict):
        return values
    for key, value in config_values.items():
        if value not in {None, ""}:
            values[str(key)] = str(value)
    return values


def _requirements_packages() -> set[str]:
    if not REQUIREMENTS_PATH.is_file():
        return set()
    packages: set[str] = set()
    for raw_line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        package_name = re.split(r"[<>=~!; \[]", line, maxsplit=1)[0].strip().lower()
        if package_name:
            packages.add(package_name)
    return packages


def check_required_dependency_env() -> list[str]:
    values = _read_config_values()
    packages = _requirements_packages()
    failures: list[str] = []

    for package_name, required_groups in DEPENDENCY_ENV_REQUIREMENTS.items():
        if package_name not in packages:
            continue
        for required_group in required_groups:
            if any(values.get(key) for key in required_group):
                continue
            failures.append(
                f"{package_name} is installed but none of these config/.env values are set: "
                f"{', '.join(required_group)}"
            )

    return failures


def _configured_path(value: str) -> Path:
    return Path(_expand_env_vars(value)).expanduser()


def check_tesseract_path() -> list[str]:
    values = _read_config_values()
    tesseract_path = values.get("TESSERACT_PATH")
    if not tesseract_path:
        return ["TESSERACT_PATH is missing from config.json or .env"]

    resolved = _configured_path(tesseract_path)
    if not resolved.is_file():
        return [f"TESSERACT_PATH is not accessible: {resolved}"]
    return []


def check_runtime_health() -> list[str]:
    failures: list[str] = []
    values = _read_config_values()
    window_title = values.get("ROK_WINDOW_TITLE") or values.get("WINDOW_TITLE") or "Rise of Kingdoms"

    tesseract_failures = check_tesseract_path()
    failures.extend(tesseract_failures)

    openai_key = values.get("OPENAI_KEY") or values.get("OPENAI_API_KEY")
    if not openai_key:
        failures.append("OPENAI_KEY or OPENAI_API_KEY is missing from config.json or .env")
    else:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=openai_key)
            _ = client.responses
        except Exception as exc:
            failures.append(f"OpenAI Responses API is unavailable: {exc}")

    try:
        from window_handler import WindowHandler, win32gui
    except Exception as exc:
        return failures + [f"Unable to import WindowHandler for runtime health check: {exc}"]

    handler = WindowHandler()
    if not handler._win32_available():
        failures.append("pywin32 is unavailable; background capture and virtual input cannot run")
        return failures

    window = handler.get_window(window_title)
    if not window:
        failures.append(f"Target game window is not reachable: {window_title!r}")
        return failures

    hwnd = int(window._hWnd)
    if not win32gui.IsWindow(hwnd):
        failures.append(f"Target hwnd is not a valid window handle: {hwnd}")
        return failures

    if win32gui.IsIconic(hwnd):
        handler.activate_window(window_title)
    if win32gui.IsIconic(hwnd):
        failures.append(f"Target window is minimized and could not be restored without activation: {window_title!r}")

    client_rect = handler.get_client_window_rect(window_title)
    if client_rect is None:
        failures.append(f"Unable to read client rectangle for target hwnd: {hwnd}")
    elif client_rect.width <= 0 or client_rect.height <= 0:
        failures.append(
            f"Target window client area is invalid: {client_rect.width}x{client_rect.height}"
        )

    return failures


def check_optional_yolo_detector() -> list[str]:
    values = _read_config_values()
    weights_path = values.get("ROK_YOLO_WEIGHTS")
    if not weights_path:
        return []

    resolved = _configured_path(weights_path)
    if not resolved.is_file():
        return [f"ROK_YOLO_WEIGHTS is configured but not accessible: {resolved}"]

    try:
        import ultralytics  # noqa: F401
    except Exception as exc:
        return [f"ROK_YOLO_WEIGHTS is configured but ultralytics is unavailable: {exc}"]

    return []


def check_interception_input() -> list[str]:
    try:
        import interception
    except Exception as exc:
        return [
            "interception-python is unavailable. Install it with requirements.txt, then install the "
            f"Oblita Interception driver as Administrator and reboot. Details: {exc}"
        ]

    try:
        try:
            interception.auto_capture_devices(keyboard=True, mouse=True)
        except TypeError:
            interception.auto_capture_devices()
    except Exception as exc:
        return [
            "Interception could not hook keyboard/mouse devices. Install the Oblita Interception "
            f"driver as Administrator and reboot before running foreground hardware input. Details: {exc}"
        ]

    return []


def check_planner_modules() -> list[str]:
    failures: list[str] = []
    for module_name in ["dynamic_planner", "ocr_service", "vision_memory", "Actions.dynamic_planner_action"]:
        try:
            __import__(module_name)
        except Exception as exc:
            failures.append(f"Unable to import {module_name}: {exc}")
    return failures


def _truthy(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def check_watchdog_module() -> list[str]:
    failures: list[str] = []
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    try:
        __import__("watchdog")
    except Exception as exc:
        failures.append(f"Unable to import watchdog.py: {exc}")

    values = _read_config_values()
    restart_explicitly_enabled = _truthy(values.get("WATCHDOG_RESTART_ENABLED"))
    client_path = values.get("ROK_CLIENT_PATH")
    if not client_path:
        message = "ROK_CLIENT_PATH is missing; watchdog can restart the UI but cannot relaunch the game"
        if restart_explicitly_enabled:
            failures.append(message)
        else:
            LOGGER.warning(message)
        return failures

    resolved = _configured_path(client_path)
    if not resolved.is_file():
        failures.append(f"ROK_CLIENT_PATH is configured but not accessible: {resolved}")

    return failures


def check_ui_map_coordinates() -> list[str]:
    failures: list[str] = []
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
        if not all(isinstance(item, int | float) for item in value):
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
        "UIMap coordinates": check_ui_map_coordinates,
        "required dependency environment": check_required_dependency_env,
        "runtime health": check_runtime_health,
        "Interception hardware input": check_interception_input,
        "guarded planner modules": check_planner_modules,
        "watchdog module": check_watchdog_module,
        "optional YOLO detector": check_optional_yolo_detector,
    }

    failures: list[str] = []
    for label, check in checks.items():
        try:
            check_failures = check()
        except Exception as exc:
            check_failures = [f"{label} check crashed: {exc}"]

        if check_failures:
            LOGGER.error("[FAIL] %s", label)
            for failure in check_failures:
                LOGGER.error("  - %s", failure)
            failures.extend(check_failures)
        else:
            LOGGER.info("[OK] %s", label)

    if failures:
        LOGGER.error("Integrity check failed with %s issue(s).", len(failures))
        return 1

    LOGGER.info("Integrity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
