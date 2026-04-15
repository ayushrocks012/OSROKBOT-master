import threading
import time

from context import Context
from signal_emitter import SignalEmitter


class OSROKBOT:
    def __init__(self, window_title, delay=1):
        self.window_title = window_title
        self.delay = delay
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.signal_emitter = SignalEmitter()
        self.is_running = False
        self.all_threads_joined = True
        self._runner_thread = None

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
