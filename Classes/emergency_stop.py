import os
import threading

from termcolor import colored


class EmergencyStop:
    _lock = threading.Lock()
    _started = False
    _error = None

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

            try:
                keyboard.add_hotkey("f12", cls._kill_now, suppress=False)
            except Exception as exc:
                cls._error = exc
                print(colored(f"Emergency F12 stop hook failed: {exc}", "red"))
                return False

            cls._started = True
            cls._error = None
            print(colored("Emergency stop armed: press F12 to immediately terminate OSROKBOT.", "yellow"))
            return True

    @staticmethod
    def _kill_now():
        print(colored("Emergency F12 stop triggered. Terminating OSROKBOT now.", "red"))
        os._exit(0)
