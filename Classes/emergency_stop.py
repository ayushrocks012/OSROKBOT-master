import os
import threading
import time

from termcolor import colored


class EmergencyStop:
    """Process-level F12 kill switch for hardware-input emergencies.

    Interception physically controls the real mouse. The emergency stop must
    therefore be independent from PyQt button handlers and the bot pause/stop
    events. This class arms both the `keyboard` hotkey callback and a small
    daemon polling loop. Either path calls `os._exit(0)` immediately.
    """

    _lock = threading.Lock()
    _started = False
    _error = None
    _keyboard = None
    _exit_func = staticmethod(os._exit)
    _poll_delay = 0.05

    @classmethod
    def start_once(cls):
        with cls._lock:
            if cls._started:
                return True
            try:
                import keyboard
            except Exception as exc:
                cls._error = exc
                print(colored(f"Emergency F12 stop unavailable: {exc}", "red"))
                return False
            cls._keyboard = keyboard

            try:
                keyboard.add_hotkey("f12", cls._kill_now, suppress=False)
            except Exception as exc:
                cls._error = exc
                print(colored(f"Emergency F12 stop hook failed: {exc}", "red"))
            else:
                cls._error = None

            poll_thread = threading.Thread(target=cls._poll_loop, name="OSROKBOT-F12-Kill", daemon=True)
            poll_thread.start()

            cls._started = True
            print(colored("Emergency stop armed: press F12 to immediately terminate OSROKBOT.", "yellow"))
            return True

    @classmethod
    def _poll_loop(cls):
        while True:
            try:
                if cls._keyboard and cls._keyboard.is_pressed("f12"):
                    cls._kill_now()
            except Exception:
                pass
            time.sleep(cls._poll_delay)

    @staticmethod
    def _kill_now():
        print(colored("Emergency F12 stop triggered. Terminating OSROKBOT now.", "red"))
        EmergencyStop._exit_func(0)
