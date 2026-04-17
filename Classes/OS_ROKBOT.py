from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any

from config_manager import ConfigManager
from context import Context
from diagnostic_screenshot import save_diagnostic_screenshot
from emergency_stop import EmergencyStop
from input_controller import InputController
from logging_config import get_logger
from object_detector import create_detector
from signal_emitter import SignalEmitter
from window_handler import WindowHandler

try:
    import win32process
except ImportError:
    win32process = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAPTCHA_LABELS = {"captcha", "captchachest", "captcha_chest"}
LOGGER = get_logger(__name__)


class OSROKBOT:
    """Executor-backed runner for one or more automation state machines.

    The runner owns pause/stop events, injects a shared Context, and performs
    foreground/captcha safety checks before each workflow step. Planner and
    YOLO/VLM recovery now handle visible prompts without gameplay media assets.
    """

    def __init__(self, window_title: str, delay: float = 1) -> None:
        self.window_title = window_title
        self.delay = delay
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.signal_emitter = SignalEmitter()
        self.is_running = False
        self.all_threads_joined = True
        self._runner_executor: ThreadPoolExecutor | None = None
        self._runner_future: Future[None] | None = None
        self.window_handler = WindowHandler()
        self.input_controller = InputController(context=None)
        self.detector: Any = create_detector()
        self._heartbeat_lock = threading.Lock()
        self._last_heartbeat_at = 0.0
        self._heartbeat_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OSROKBOT-Heartbeat")
        self._heartbeat_future: Future[None] | None = None
        self._cached_game_pid: int | None = None
        self._cached_game_pid_window_title: str | None = None
        self._cached_game_pid_hwnd: int | None = None

    def _emit_state(self, context: Context | None, state_text: str) -> None:
        if context:
            context.emit_state(state_text)
        elif self.signal_emitter:
            self.signal_emitter.state_changed.emit(state_text)

    def _hardware_input_ready(self, context: Context | None = None) -> bool:
        if InputController.is_backend_available():
            return True
        message = InputController.backend_error()
        LOGGER.error("Interception hardware input is unavailable.")
        if message:
            LOGGER.error(message)
        LOGGER.warning("Install the Oblita Interception driver as Administrator, reboot, then run OSROKBOT again.")
        self._emit_state(context, "Interception unavailable")
        return False

    def _ensure_foreground(self, context: Context | None) -> bool:
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False
        window_title = context.window_title if context and getattr(context, "window_title", None) else self.window_title
        if self.window_handler.ensure_foreground(window_title, wait_seconds=0.5):
            return True

        LOGGER.error("Game is not foreground; pausing automation before hardware input.")
        self._emit_state(context, "Game not foreground - paused")
        self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(True)
        return False

    @staticmethod
    def _config_path(value: str | os.PathLike[str] | None, default: str | os.PathLike[str]) -> Path:
        path = Path(value or default)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def _heartbeat_path(self) -> Path:
        return self._config_path(
            ConfigManager().get("WATCHDOG_HEARTBEAT_PATH"),
            PROJECT_ROOT / "data" / "heartbeat.json",
        )

    def _clear_game_pid_cache(self) -> None:
        self._cached_game_pid = None
        self._cached_game_pid_window_title = None
        self._cached_game_pid_hwnd = None

    def _game_pid(self, window_title: str) -> int | None:
        if win32process is None:
            self._clear_game_pid_cache()
            return None
        try:
            window = self.window_handler.get_window(window_title)
            if not window:
                self._clear_game_pid_cache()
                return None
            hwnd = int(window._hWnd)
            if (
                self._cached_game_pid is not None
                and self._cached_game_pid_window_title == window_title
                and self._cached_game_pid_hwnd == hwnd
            ):
                return self._cached_game_pid

            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            if not process_id:
                self._clear_game_pid_cache()
                return None

            self._cached_game_pid = int(process_id)
            self._cached_game_pid_window_title = window_title
            self._cached_game_pid_hwnd = hwnd
            return self._cached_game_pid
        except Exception:
            self._clear_game_pid_cache()
            return None

    def _heartbeat_payload(self, context: Context | None, now: float) -> dict[str, Any]:
        active_context = context or Context(bot=self, window_title=self.window_title)
        window_title = getattr(active_context, "window_title", None) or self.window_title
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "timestamp_epoch": now,
            "bot_pid": os.getpid(),
            "game_pid": self._game_pid(window_title),
            "window_title": window_title,
            "mission": getattr(active_context, "planner_goal", ""),
            "autonomy_level": getattr(active_context, "planner_autonomy_level", 1),
            "repo_root": str(PROJECT_ROOT),
            "ui_entrypoint": str(PROJECT_ROOT / "Classes" / "UI.py"),
            "python_executable": sys.executable,
        }

    @staticmethod
    def _write_heartbeat_file(heartbeat_path: Path, payload: dict[str, Any]) -> None:
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = heartbeat_path.with_suffix(heartbeat_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        for attempt in range(3):
            try:
                temp_path.replace(heartbeat_path)
                return
            except PermissionError as exc:
                if attempt == 2:
                    LOGGER.error("Failed to write heartbeat to %s due to file lock: %s", heartbeat_path, exc)
                    raise
                time.sleep(0.1)

    def _shutdown_runner_executor(self) -> None:
        executor = self._runner_executor
        self._runner_executor = None
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _log_future_exception(future: Future[Any]) -> None:
        try:
            future.result()
        except Exception as exc:
            LOGGER.warning("Background heartbeat write failed: %s", exc)

    def write_heartbeat(self, context: Context | None = None, force: bool = False) -> bool:
        now = time.time()
        with self._heartbeat_lock:
            if not force and now - self._last_heartbeat_at < 5:
                return True
            if not force and self._heartbeat_future and not self._heartbeat_future.done():
                return True

            payload = self._heartbeat_payload(context, now)
            heartbeat_path = self._heartbeat_path()
            self._last_heartbeat_at = now
            self._heartbeat_future = self._heartbeat_executor.submit(
                self._write_heartbeat_file,
                heartbeat_path,
                payload,
            )
            self._heartbeat_future.add_done_callback(self._log_future_exception)
        return True

    def _observe_window(self, context: Context):
        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        if screenshot is None or window_rect is None:
            return None

        started_at = time.perf_counter()
        try:
            detections = tuple(self.detector.detect(screenshot))
        except Exception as exc:
            LOGGER.warning("Window observation detector skipped: %s", exc)
            detections = ()
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        LOGGER.debug("YOLO observation duration_ms=%.2f detections=%s", duration_ms, len(detections))
        return context.set_current_observation(screenshot, window_rect, detections=detections)

    def _detect_captcha(self, context: Context, observation=None) -> bool:
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False

        observation = observation or getattr(context, "current_observation", None) or self._observe_window(context)
        if observation is None:
            return False

        screenshot = observation.screenshot
        detections = observation.detections
        labels = {str(getattr(detection, "label", "")).lower().replace(" ", "_") for detection in detections}
        if not labels.intersection(CAPTCHA_LABELS):
            return False

        LOGGER.error("Captcha detected: pausing automation for manual review.")
        context.emit_state("Captcha detected - paused")
        screenshot_path = save_diagnostic_screenshot(screenshot, label="captcha_detected")
        if screenshot_path and hasattr(context, "export_state_history"):
            context.export_state_history(screenshot_path.with_suffix(".log"))
        self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(True)
        return True

    def run(self, state_machines: Sequence[Any], context: Context | None = None) -> None:
        context = context or Context(bot=self, window_title=self.window_title)
        context.bot = context.bot or self
        context.signal_emitter = context.signal_emitter or self.signal_emitter
        context.window_title = context.window_title or self.window_title

        self.stop_event.clear()
        self.all_threads_joined = False
        self.write_heartbeat(context, force=True)

        def run_single_machine(machine: Any) -> None:
            while not self.stop_event.is_set():
                observation = None
                if getattr(machine, "halted", False):
                    LOGGER.error("Workflow state machine halted; stopping workflow thread.")
                    break
                try:
                    if self.pause_event.is_set():
                        self.write_heartbeat(context)
                        self.stop_event.wait(self.delay)
                        continue
                    self.write_heartbeat(context)
                    if not self._ensure_foreground(context):
                        continue
                    observation = self._observe_window(context)
                    if observation is None:
                        continue
                    if self._detect_captcha(context, observation=observation):
                        continue
                    if not self._ensure_foreground(context):
                        continue
                    step_result = machine.execute(context)
                    if getattr(machine, "halted", False):
                        LOGGER.error("Workflow state machine halted after execute; stopping workflow thread.")
                        break
                    if step_result:
                        self.stop_event.wait(self.delay)
                finally:
                    if getattr(context, "current_observation", None) is observation:
                        context.clear_current_observation()

        try:
            with ThreadPoolExecutor(
                max_workers=max(1, len(state_machines)),
                thread_name_prefix="OSROKBOT-Workflow",
            ) as executor:
                futures: list[Future[None]] = [
                    executor.submit(run_single_machine, machine) for machine in state_machines
                ]
                while futures and not self.stop_event.is_set():
                    done, _pending = wait(futures, timeout=0.5, return_when=FIRST_EXCEPTION)
                    for future in done:
                        future.result()
                    futures = [future for future in futures if not future.done()]
        finally:
            self.stop_event.set()
            self.all_threads_joined = True
            self.is_running = False

    def _runner_done(self, future: Future[None]) -> None:
        failed = False
        try:
            future.result()
        except Exception as exc:
            failed = True
            LOGGER.error("OSROKBOT runner stopped after an unhandled error: %s", exc)
        if future is not self._runner_future:
            return
        if failed:
            self.stop_event.set()
        self._runner_future = None
        self.is_running = False
        self._shutdown_runner_executor()
        self.all_threads_joined = True

    def start(self, steps: Sequence[Any], context: Context | None = None) -> bool:
        if self.is_running or not self.all_threads_joined:
            return False
        if not self._hardware_input_ready(context):
            self.is_running = False
            return False
        EmergencyStop.start_once()

        self.is_running = True
        self._runner_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OSROKBOT-Runner")
        self._runner_future = self._runner_executor.submit(self.run, steps, context)
        self._runner_future.add_done_callback(self._runner_done)
        return True

    def stop(self) -> None:
        self.stop_event.set()
        self.is_running = False
        self._shutdown_runner_executor()

        # Prevent zombie threads by killing the heartbeat executor
        if getattr(self, "_heartbeat_executor", None):
            self._heartbeat_executor.shutdown(wait=False, cancel_futures=True)

    def toggle_pause(self) -> None:
        if self.pause_event.is_set():
            self.pause_event.clear()
        else:
            self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(self.pause_event.is_set())

    def is_paused(self) -> bool:
        return self.pause_event.is_set()
