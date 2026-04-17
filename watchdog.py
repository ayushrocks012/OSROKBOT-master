"""Conservative watchdog for OSROKBOT overnight runs.

The watchdog reads the heartbeat written by ``Classes/OS_ROKBOT.py`` and only
acts on the exact PIDs recorded there. It never kills processes by name.
"""

from __future__ import annotations

import argparse
import json
import os

# Watchdog launches fixed Windows utilities without shell=True.
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from typing import Any

if os.name != "nt":
    raise NotImplementedError("watchdog.py is Windows only")

PROJECT_ROOT = Path(__file__).resolve().parent
CLASSES_DIR = PROJECT_ROOT / "Classes"
DEFAULT_HEARTBEAT_PATH = PROJECT_ROOT / "data" / "heartbeat.json"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_GAME_RESTART_WAIT_SECONDS = 20.0
SUBPROCESS_TIMEOUT_SECONDS = 10.0
SYSTEM32_DIR = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
TASKLIST_EXE = SYSTEM32_DIR / "tasklist.exe"
TASKKILL_EXE = SYSTEM32_DIR / "taskkill.exe"

if str(CLASSES_DIR) not in sys.path:
    sys.path.insert(0, str(CLASSES_DIR))

from logging_config import get_logger

LOGGER = get_logger(Path(__file__).stem)

try:
    from config_manager import ConfigManager
except Exception:  # pragma: no cover - import failure is reported at runtime.
    ConfigManager = None

try:
    import win32gui
except Exception:  # pragma: no cover - pywin32 is optional for unit tests.
    win32gui = None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _configured_value(key: str, default: Any = None) -> Any:
    if ConfigManager is None:
        return os.getenv(key, default)
    return ConfigManager().get(key, default)


def _configured_path(key: str, default: Path | None = None) -> Path | None:
    value = _configured_value(key)
    if value:
        return Path(os.path.expandvars(str(value))).expanduser()
    return default


def heartbeat_path_from_config() -> Path:
    return _configured_path("WATCHDOG_HEARTBEAT_PATH", DEFAULT_HEARTBEAT_PATH) or DEFAULT_HEARTBEAT_PATH


def timeout_from_config() -> float:
    try:
        return float(_configured_value("WATCHDOG_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def game_restart_wait_from_config() -> float:
    try:
        return float(_configured_value("WATCHDOG_GAME_RESTART_WAIT_SECONDS", DEFAULT_GAME_RESTART_WAIT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_GAME_RESTART_WAIT_SECONDS


def restart_enabled_from_config() -> bool:
    value = _configured_value("WATCHDOG_RESTART_ENABLED", "1")
    return _truthy(value)


def read_heartbeat(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOGGER.warning("Heartbeat not found yet: %s", path)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.error("Unable to read heartbeat %s: %s", path, exc)
        return None

    if not isinstance(payload, dict):
        LOGGER.error("Heartbeat is not a JSON object: %s", path)
        return None
    return payload


def heartbeat_age_seconds(payload: dict[str, Any], now: float | None = None) -> float:
    now = time.time() if now is None else now
    timestamp = payload.get("timestamp_epoch")
    try:
        return max(0.0, now - float(timestamp))
    except (TypeError, ValueError):
        return float("inf")


def safe_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or pid == os.getpid():
        return None
    return pid


def is_pid_running(pid: int) -> bool:
    try:
        result = subprocess.run(
            [str(TASKLIST_EXE), "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )  # nosec B603
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return f'"{pid}"' in result.stdout


def terminate_tracked_pid(pid_value: Any, label: str) -> bool:
    pid = safe_pid(pid_value)
    if pid is None:
        LOGGER.warning("Skipping invalid %s PID: %r", label, pid_value)
        return False

    LOGGER.warning("Terminating tracked %s PID %s", label, pid)
    try:
        subprocess.run(
            [str(TASKKILL_EXE), "/PID", str(pid), "/T", "/F"],
            check=False,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )  # nosec B603
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        LOGGER.error("Unable to terminate %s PID %s: %s", label, pid, exc)
        return False
    return True


def find_window_by_title(title: str | None) -> int | None:
    if win32gui is None or not title:
        return None

    target = title.lower()
    found: list[int] = []

    def callback(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        window_text = win32gui.GetWindowText(hwnd)
        if target in window_text.lower():
            found.append(hwnd)
            return False
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except (OSError, RuntimeError):
        return None
    return found[0] if found else None


def game_is_missing(payload: dict[str, Any]) -> bool:
    game_pid = safe_pid(payload.get("game_pid"))
    if game_pid is not None and is_pid_running(game_pid):
        return False

    window_title = str(payload.get("window_title") or "").strip()
    return not (window_title and find_window_by_title(window_title))


def relaunch_game() -> bool:
    client_path = _configured_path("ROK_CLIENT_PATH")
    if not client_path:
        LOGGER.warning("ROK_CLIENT_PATH is not configured; game relaunch skipped.")
        return False
    if not client_path.is_file():
        LOGGER.error("ROK_CLIENT_PATH is not accessible; game relaunch skipped: %s", client_path)
        return False

    LOGGER.info("Launching Rise of Kingdoms from %s", client_path)
    try:
        # Explicit configured executable path, no shell.
        subprocess.Popen([str(client_path)], cwd=str(client_path.parent))  # nosec B603
    except (OSError, ValueError) as exc:
        LOGGER.error("Unable to relaunch game: %s", exc)
        return False
    return True


def restart_ui_from_heartbeat(payload: dict[str, Any]) -> bool:
    python_executable = str(payload.get("python_executable") or sys.executable)
    ui_entrypoint = Path(str(payload.get("ui_entrypoint") or PROJECT_ROOT / "Classes" / "UI.py"))
    repo_root = Path(str(payload.get("repo_root") or PROJECT_ROOT))

    if not Path(python_executable).name:
        LOGGER.error("Python executable in heartbeat is invalid; UI restart skipped: %r", python_executable)
        return False
    if not ui_entrypoint.is_file():
        LOGGER.error("UI entrypoint is not accessible; UI restart skipped: %s", ui_entrypoint)
        return False
    if not repo_root.is_dir():
        LOGGER.error("Repository root in heartbeat is not accessible; UI restart skipped: %s", repo_root)
        return False

    LOGGER.info("Restarting OSROKBOT UI with %s", python_executable)
    try:
        # Heartbeat records exact executable, no shell.
        subprocess.Popen([python_executable, str(ui_entrypoint)], cwd=str(repo_root))  # nosec B603
    except (OSError, ValueError) as exc:
        LOGGER.error("Unable to restart OSROKBOT UI: %s", exc)
        return False
    return True


def handle_stale_heartbeat(payload: dict[str, Any]) -> bool:
    if not restart_enabled_from_config():
        LOGGER.warning("Watchdog restart is disabled by WATCHDOG_RESTART_ENABLED.")
        return False

    terminate_tracked_pid(payload.get("bot_pid"), "bot")
    terminate_tracked_pid(payload.get("game_pid"), "game")

    relaunch_game()
    time.sleep(game_restart_wait_from_config())
    return restart_ui_from_heartbeat(payload)


def check_once(heartbeat_path: Path, timeout_seconds: float, now: float | None = None) -> bool:
    payload = read_heartbeat(heartbeat_path)
    if payload is None:
        return False

    age = heartbeat_age_seconds(payload, now=now)
    if age > timeout_seconds:
        LOGGER.error("Heartbeat is stale (%.1fs old); restarting tracked bot/game.", age)
        return handle_stale_heartbeat(payload)

    if game_is_missing(payload):
        LOGGER.warning("Game process/window is missing while bot heartbeat is fresh.")
        return relaunch_game()

    LOGGER.info("Heartbeat is fresh (%.1fs old); no watchdog action needed.", age)
    return True


def run_daemon(heartbeat_path: Path, timeout_seconds: float) -> None:
    LOGGER.info("OSROKBOT watchdog watching %s", heartbeat_path)
    while True:
        check_once(heartbeat_path, timeout_seconds)
        time.sleep(5)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OSROKBOT conservative heartbeat watchdog.")
    parser.add_argument("--heartbeat", type=Path, default=heartbeat_path_from_config())
    parser.add_argument("--timeout", type=float, default=timeout_from_config())
    parser.add_argument("--once", action="store_true", help="Run one watchdog check and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    heartbeat_path = Path(args.heartbeat)
    timeout_seconds = float(args.timeout)

    if args.once:
        return 0 if check_once(heartbeat_path, timeout_seconds) else 1

    run_daemon(heartbeat_path, timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
