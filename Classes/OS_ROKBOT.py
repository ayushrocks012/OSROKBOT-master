import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from config_manager import ConfigManager
from context import Context
from emergency_stop import EmergencyStop
from image_finder import ImageFinder
from input_controller import InputController
from object_detector import create_detector
from signal_emitter import SignalEmitter
from termcolor import colored
from window_handler import WindowHandler

try:
    import win32process
except ImportError:
    win32process = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAPTCHA_LABELS = {"captcha", "captchachest", "captcha_chest"}


class OSROKBOT:
    """Threaded runner for one or more automation state machines.

    The runner owns pause/stop events, injects a shared Context, and performs a
    pre-action blocker sweep before each workflow step. Known blockers are
    matched with ImageFinder and dismissed through InputController so click
    bounds validation and pause/abort interlocks are enforced consistently.
    """

    def __init__(self, window_title, delay=1):
        self.window_title = window_title
        self.delay = delay
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.signal_emitter = SignalEmitter()
        self.is_running = False
        self.all_threads_joined = True
        self._runner_thread = None
        self.window_handler = WindowHandler()
        self.input_controller = InputController(context=None)
        self.diagnostic_finder = ImageFinder(threshold=0.85, save_heatmaps=False)
        self.detector = create_detector()
        self._heartbeat_lock = threading.Lock()
        self._last_heartbeat_at = 0.0

    def _emit_state(self, context, state_text):
        if context:
            context.emit_state(state_text)
        elif self.signal_emitter:
            self.signal_emitter.state_changed.emit(state_text)

    def _hardware_input_ready(self, context=None):
        if InputController.is_backend_available():
            return True
        message = InputController.backend_error()
        print(colored("Interception hardware input is unavailable.", "red"))
        if message:
            print(colored(message, "red"))
        print(colored("Install the Oblita Interception driver as Administrator, reboot, then run OSROKBOT again.", "yellow"))
        self._emit_state(context, "Interception unavailable")
        return False

    def _ensure_foreground(self, context):
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False
        window_title = context.window_title if context and getattr(context, "window_title", None) else self.window_title
        if self.window_handler.ensure_foreground(window_title, wait_seconds=0.5):
            return True

        print(colored("Game is not foreground; pausing automation before hardware input.", "red"))
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

    def write_heartbeat(self, context=None, force=False):
        now = time.time()
        if not force and now - self._last_heartbeat_at < 5:
            return True

        active_context = context or Context(bot=self, window_title=self.window_title)
        window_title = getattr(active_context, "window_title", None) or self.window_title
        payload = {
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
        heartbeat_path = self._heartbeat_path()

        try:
            with self._heartbeat_lock:
                heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = heartbeat_path.with_suffix(heartbeat_path.suffix + ".tmp")
                temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                temp_path.replace(heartbeat_path)
                self._last_heartbeat_at = now
            return True
        except Exception as exc:
            print(colored(f"Unable to write watchdog heartbeat: {exc}", "yellow"))
            return False

    def _detect_captcha(self, context):
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False

        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        if screenshot is None or window_rect is None:
            return False

        try:
            detections = self.detector.detect(screenshot)
        except Exception as exc:
            print(colored(f"Captcha detector skipped: {exc}", "yellow"))
            return False

        labels = {str(getattr(detection, "label", "")).lower().replace(" ", "_") for detection in detections}
        if not labels.intersection(CAPTCHA_LABELS):
            return False

        print(colored("Captcha detected: pausing automation for manual review.", "red"))
        context.emit_state("Captcha detected - paused")
        screenshot_path = self.diagnostic_finder.save_screenshot(screenshot, label="captcha_detected")
        if screenshot_path and hasattr(context, "export_state_history"):
            context.export_state_history(screenshot_path.with_suffix(".log"))
        self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(True)
        return True

    def _clear_global_blockers(self, context):
        """Legacy template blockers are purged; planner handles visible modals."""
        return True

    def _locate_primary_anchor(self, context):
        """Primary anchors are now inferred by YOLO/VLM instead of templates."""
        return False

    def run(self, state_machines, context=None):
        context = context or Context(bot=self, window_title=self.window_title)
        context.bot = context.bot or self
        context.signal_emitter = context.signal_emitter or self.signal_emitter
        context.window_title = context.window_title or self.window_title

        self.stop_event.clear()
        self.all_threads_joined = False
        self.write_heartbeat(context, force=True)
        self._locate_primary_anchor(context)

        def run_single_machine(machine):
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    self.write_heartbeat(context)
                    time.sleep(self.delay)
                    continue
                self.write_heartbeat(context)
                if not self._ensure_foreground(context):
                    continue
                if self._detect_captcha(context):
                    continue
                if not self._ensure_foreground(context):
                    continue
                if not self._clear_global_blockers(context):
                    continue
                if not self._ensure_foreground(context):
                    continue
                if machine.execute(context):
                    time.sleep(self.delay)

        threads = [
            threading.Thread(target=run_single_machine, args=(machine,), daemon=True)
            for machine in state_machines
        ]

        try:
            for thread in threads:
                thread.start()

            for thread in threads:
                thread.join()
        finally:
            self.all_threads_joined = True
            self.is_running = False

    def start(self, steps, context=None):
        if self.is_running or not self.all_threads_joined:
            return False
        if not self._hardware_input_ready(context):
            self.is_running = False
            return False
        EmergencyStop.start_once()

        self.is_running = True
        self._runner_thread = threading.Thread(
            target=self.run,
            args=(steps, context),
            daemon=True,
        )
        self._runner_thread.start()
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
