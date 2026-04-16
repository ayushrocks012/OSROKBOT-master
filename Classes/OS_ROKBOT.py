import threading
import time
from pathlib import Path

from context import Context
from emergency_stop import EmergencyStop
from image_finder import ImageFinder
from input_controller import InputController
from signal_emitter import SignalEmitter
from termcolor import colored
from window_handler import WindowHandler


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_BLOCKER_IMAGES = (
    PROJECT_ROOT / "Media" / "confirm.png",
    PROJECT_ROOT / "Media" / "escx.png",
)
CAPTCHA_IMAGE = PROJECT_ROOT / "Media" / "captchachest.png"


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
        self.blocker_finder = ImageFinder(threshold=0.85, save_heatmaps=False)
        self.global_blocker_images = tuple(str(path) for path in GLOBAL_BLOCKER_IMAGES)
        self.captcha_image = str(CAPTCHA_IMAGE)

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

    def _detect_captcha(self, context):
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False
        if not CAPTCHA_IMAGE.is_file():
            return False

        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        if screenshot is None or window_rect is None:
            return False

        found, _, _, _ = self.blocker_finder.find_image_coordinates(
            self.captcha_image,
            screenshot,
            window_rect,
            0,
            0,
            1,
        )
        if not found:
            return False

        print(colored("Captcha detected: pausing automation for manual review.", "red"))
        context.emit_state("Captcha detected - paused")
        screenshot_path = self.blocker_finder.save_screenshot(screenshot, label="captcha_detected")
        if screenshot_path and hasattr(context, "export_state_history"):
            context.export_state_history(screenshot_path.with_suffix(".log"))
        self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(True)
        return True

    def _clear_global_blockers(self, context):
        """Dismiss known modal blockers before the next workflow action runs."""
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False

        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        if screenshot is None or window_rect is None:
            return True

        for blocker_path in self.global_blocker_images:
            if self.stop_event.is_set() or self.pause_event.is_set():
                return False

            found, click_x, click_y, _ = self.blocker_finder.find_image_coordinates(
                blocker_path,
                screenshot,
                window_rect,
                0,
                0,
                1,
            )
            if not found or click_x is None or click_y is None:
                continue

            print(colored(f"Pre-action blocker cleared: {Path(blocker_path).name}", "yellow"))
            context.emit_state(f"Clearing blocker\n{Path(blocker_path).name}")
            if not self.input_controller.click(
                click_x,
                click_y,
                window_rect=window_rect,
                context=context,
            ):
                return False

            self.input_controller.wait(0.2, context=context)
            screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
            if screenshot is None or window_rect is None:
                return True

        return True

    def _locate_primary_anchor(self, context):
        anchor_image = getattr(context, "primary_anchor_image", None)
        if not anchor_image:
            return False

        anchor_path = Path(anchor_image)
        if not anchor_path.is_absolute():
            anchor_path = PROJECT_ROOT / anchor_path
        if not anchor_path.is_file():
            print(colored(f"Primary UI anchor template is missing: {anchor_path}", "yellow"))
            return False

        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        if screenshot is None or window_rect is None:
            return False

        return self.blocker_finder.locate_primary_anchor(
            str(anchor_path),
            screenshot,
            window_rect,
            context,
            anchor_name=getattr(context, "primary_ui_anchor", "primary"),
            reference_normalized=getattr(context, "primary_anchor_reference_normalized", None),
        )

    def run(self, state_machines, context=None):
        context = context or Context(bot=self, window_title=self.window_title)
        context.bot = context.bot or self
        context.signal_emitter = context.signal_emitter or self.signal_emitter
        context.window_title = context.window_title or self.window_title

        self.stop_event.clear()
        self.all_threads_joined = False
        self._locate_primary_anchor(context)

        def run_single_machine(machine):
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(self.delay)
                    continue
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
