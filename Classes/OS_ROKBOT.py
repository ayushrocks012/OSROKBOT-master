import threading
import time
from pathlib import Path

from context import Context
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
        self.blocker_finder = ImageFinder(threshold=0.85)
        self.global_blocker_images = tuple(str(path) for path in GLOBAL_BLOCKER_IMAGES)

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

    def run(self, state_machines, context=None):
        context = context or Context(bot=self, window_title=self.window_title)
        context.bot = context.bot or self
        context.signal_emitter = context.signal_emitter or self.signal_emitter
        context.window_title = context.window_title or self.window_title

        self.stop_event.clear()
        self.all_threads_joined = False

        def run_single_machine(machine):
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    time.sleep(self.delay)
                    continue
                if not self._clear_global_blockers(context):
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
