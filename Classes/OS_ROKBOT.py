import json
import os
import sys
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

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

    def __init__(self, window_title, delay=1):
        self.window_title = window_title
        self.delay = delay
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.signal_emitter = SignalEmitter()
        self.is_running = False
        self.all_threads_joined = True
        self._runner_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OSROKBOT-Runner")
        self._runner_future: Future | None = None
        self.window_handler = WindowHandler()
        self.input_controller = InputController(context=None)
        self.detector = create_detector()
        self._heartbeat_lock = threading.Lock()
        self._last_heartbeat_at = 0.0
        self._heartbeat_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OSROKBOT-Heartbeat")
        self._heartbeat_future: Future | None = None

    def _emit_state(self, context, state_text):
        if context:
            context.emit_state(state_text)
        elif self.signal_emitter:
            self.signal_emitter.state_changed.emit(state_text)

    def _hardware_input_ready(self, context=None):
        if InputController.is_backend_available():
            return True
        message = InputController.backend_error()
        LOGGER.error("Interception hardware input is unavailable.")
        if message:
            LOGGER.error(message)
        LOGGER.warning("Install the Oblita Interception driver as Administrator, reboot, then run OSROKBOT again.")
        self._emit_state(context, "Interception unavailable")
        return False

    def _ensure_foreground(self, context):
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
    def _config_path(value, default):
        path = Path(value or default)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def _heartbeat_path(self):
        return self._config_path(
            ConfigManager().get("WATCHDOG_HEARTBEAT_PATH"),
            PROJECT_ROOT / "data" / "heartbeat.json",
        )

    def _game_pid(self, window_title):
        if win32process is None:
            return None
        try:
            window = self.window_handler.get_window(window_title)
            if not window:
                return None
            _, process_id = win32process.GetWindowThreadProcessId(int(window._hWnd))
            return int(process_id) if process_id else None
        except Exception:
            return None

    def _heartbeat_payload(self, context, now):
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
    def _write_heartbeat_file(heartbeat_path, payload):
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = heartbeat_path.with_suffix(heartbeat_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(heartbeat_path)

    @staticmethod
    def _log_future_exception(future):
        try:
            future.result()
        except Exception as exc:
            LOGGER.warning("Background heartbeat write failed: %s", exc)

    def write_heartbeat(self, context=None, force=False):
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

    def _detect_captcha(self, context):
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False

        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        if screenshot is None or window_rect is None:
            return False

        try:
            detections = self.detector.detect(screenshot)
        except Exception as exc:
            LOGGER.warning("Captcha detector skipped: %s", exc)
            return False

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

    def run(self, state_machines, context=None):
        context = context or Context(bot=self, window_title=self.window_title)
        context.bot = context.bot or self
        context.signal_emitter = context.signal_emitter or self.signal_emitter
        context.window_title = context.window_title or self.window_title

        self.stop_event.clear()
        self.all_threads_joined = False
        self.write_heartbeat(context, force=True)

        def run_single_machine(machine):
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    self.write_heartbeat(context)
                    self.stop_event.wait(self.delay)
                    continue
                self.write_heartbeat(context)
                if not self._ensure_foreground(context):
                    continue
                if self._detect_captcha(context):
                    continue
                if not self._ensure_foreground(context):
                    continue
                if machine.execute(context):
                    self.stop_event.wait(self.delay)

        try:
            with ThreadPoolExecutor(
                max_workers=max(1, len(state_machines)),
                thread_name_prefix="OSROKBOT-Workflow",
            ) as executor:
                futures = [executor.submit(run_single_machine, machine) for machine in state_machines]
                while futures and not self.stop_event.is_set():
                    done, _pending = wait(futures, timeout=0.5, return_when=FIRST_EXCEPTION)
                    for future in done:
                        future.result()
                    futures = [future for future in futures if not future.done()]
        finally:
            self.stop_event.set()
            self.all_threads_joined = True
            self.is_running = False

    def _runner_done(self, future):
        try:
            future.result()
        except Exception as exc:
            LOGGER.error("OSROKBOT runner stopped after an unhandled error: %s", exc)
            self.stop_event.set()
            self.is_running = False
            self.all_threads_joined = True

    def start(self, steps, context=None):
        if self.is_running or not self.all_threads_joined:
            return False
        if not self._hardware_input_ready(context):
            self.is_running = False
            return False
        EmergencyStop.start_once()

        self.is_running = True
        self._runner_future = self._runner_executor.submit(self.run, steps, context)
        self._runner_future.add_done_callback(self._runner_done)
        return True

    def stop(self):
        self.stop_event.set()
        self.is_running = False

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
        else:
            self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(self.pause_event.is_set())

    def is_paused(self):
        return self.pause_event.is_set()
