import ctypes
import math
import random
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from termcolor import colored

try:
    import interception
except ImportError as exc:
    interception = None
    INTERCEPTION_IMPORT_ERROR = exc
else:
    INTERCEPTION_IMPORT_ERROR = None


class WindowRect(Protocol):
    hwnd: int
    left: int
    top: int
    width: int
    height: int


@dataclass
class DelayPolicy:
    """Centralized bounded pacing for UI interactions."""

    default_delay: float = 0.0
    click_settle_delay: float = 0.1
    key_hold_delay: float = 1.0
    scroll_settle_delay: float = 0.05
    poll_delay: float = 0.1
    jitter_ratio: float = 0.15

    def adjusted_delay(self, seconds: Optional[float] = None):
        delay = self.default_delay if seconds is None else seconds
        if delay <= 0:
            return 0.0
        jitter = abs(delay) * max(0.0, self.jitter_ratio)
        return max(0.0, random.uniform(delay - jitter, delay + jitter))

    def wait(self, seconds: Optional[float] = None, context=None):
        delay = self.adjusted_delay(seconds)
        if delay <= 0:
            return True

        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if not InputController.is_allowed(context):
                return False
            time.sleep(min(self.poll_delay, deadline - time.monotonic()))
        return True


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class InputController:
    KEY_ALIASES = {
        "escape": "esc",
        "esc": "esc",
        "space": "space",
        "enter": "enter",
        "return": "enter",
        "tab": "tab",
        "backspace": "backspace",
        "left": "left",
        "up": "up",
        "right": "right",
        "down": "down",
        "shift": "shift",
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
    }

    _capture_attempted = False
    _capture_ready = False
    _capture_error = None

    def __init__(
        self,
        delay_policy: Optional[DelayPolicy] = None,
        context=None,
        coordinate_noise_px=3,
        move_duration=0.18,
        move_steps_per_second=60,
    ):
        self.delay_policy = delay_policy or DelayPolicy()
        self.context = context
        self.coordinate_noise_px = max(0, int(coordinate_noise_px))
        self.move_duration = max(0.0, float(move_duration))
        self.move_steps_per_second = max(10, int(move_steps_per_second))
        self.ensure_interception_ready()

    @classmethod
    def ensure_interception_ready(cls):
        if cls._capture_attempted:
            return cls._capture_ready

        cls._capture_attempted = True
        if interception is None:
            cls._capture_error = f"interception-python is not installed: {INTERCEPTION_IMPORT_ERROR}"
            cls._capture_ready = False
            return False

        try:
            try:
                interception.auto_capture_devices(keyboard=True, mouse=True)
            except TypeError:
                interception.auto_capture_devices()
        except Exception as exc:
            cls._capture_error = (
                "Interception driver failed to hook devices. Install the Oblita "
                f"Interception driver as Administrator and reboot. Details: {exc}"
            )
            cls._capture_ready = False
            return False

        cls._capture_error = None
        cls._capture_ready = True
        return True

    @classmethod
    def is_backend_available(cls):
        return cls.ensure_interception_ready()

    @classmethod
    def backend_error(cls):
        cls.ensure_interception_ready()
        return cls._capture_error or ""

    @staticmethod
    def is_allowed(context=None):
        if not context or not getattr(context, "bot", None):
            return True
        bot = context.bot
        if getattr(bot, "stop_event", None) and bot.stop_event.is_set():
            return False
        if getattr(bot, "pause_event", None) and bot.pause_event.is_set():
            return False
        return True

    def _context(self, context=None):
        return context or self.context

    def check_interlock(self, context=None):
        active_context = self._context(context)
        if self.is_allowed(active_context):
            return True
        print(colored("Input blocked: bot is paused or stopping.", "yellow"))
        return False

    def check_backend(self):
        if self.ensure_interception_ready():
            return True
        print(colored(f"Hardware input blocked: {self.backend_error()}", "red"))
        return False

    @staticmethod
    def _pause_for_foreground_failure(context):
        if not context:
            return
        context.emit_state("Game not foreground - paused")
        bot = getattr(context, "bot", None)
        if bot and getattr(bot, "pause_event", None):
            bot.pause_event.set()
            if getattr(bot, "signal_emitter", None):
                bot.signal_emitter.pause_toggled.emit(True)

    def check_foreground(self, context=None):
        active_context = self._context(context)
        window_title = getattr(active_context, "window_title", None) if active_context else None
        if not window_title:
            return True
        try:
            from window_handler import WindowHandler

            if WindowHandler().ensure_foreground(window_title, wait_seconds=0.5):
                return True
        except Exception as exc:
            print(colored(f"Foreground input guard failed: {exc}", "red"))

        print(colored("Hardware input blocked: target game window is not foreground.", "red"))
        self._pause_for_foreground_failure(active_context)
        return False

    @staticmethod
    def validate_bounds(x, y, window_rect):
        if not window_rect:
            return False
        return (
            int(window_rect.left) <= int(x) <= int(window_rect.left + window_rect.width)
            and int(window_rect.top) <= int(y) <= int(window_rect.top + window_rect.height)
        )

    def wait(self, seconds=0, context=None):
        return self.delay_policy.wait(seconds, self._context(context))

    @staticmethod
    def _clamp(value, minimum, maximum):
        return max(minimum, min(maximum, value))

    @staticmethod
    def _virtual_key(key):
        if isinstance(key, int):
            return key
        normalized = str(key).lower()
        if normalized in InputController.KEY_ALIASES:
            return InputController.KEY_ALIASES[normalized]
        if normalized.startswith("f") and normalized[1:].isdigit():
            number = int(normalized[1:])
            if 1 <= number <= 24:
                return f"f{number}"
        if len(normalized) == 1:
            return normalized
        raise ValueError(f"Unsupported key for hardware input: {key}")

    @staticmethod
    def _calculate_bezier_point(start, ctrl1, ctrl2, end, t):
        return (
            (1 - t) ** 3 * start
            + 3 * (1 - t) ** 2 * t * ctrl1
            + 3 * (1 - t) * t**2 * ctrl2
            + t**3 * end
        )

    @staticmethod
    def _desktop_mouse_position():
        point = _Point()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return int(point.x), int(point.y)
        return 0, 0

    @staticmethod
    def _mouse_position():
        if interception is not None:
            for name in ("mouse_position", "get_mouse_position"):
                getter = getattr(interception, name, None)
                if getter:
                    position = getter()
                    if isinstance(position, tuple) and len(position) >= 2:
                        return int(position[0]), int(position[1])
                    if hasattr(position, "x") and hasattr(position, "y"):
                        return int(position.x), int(position.y)
        return InputController._desktop_mouse_position()

    @staticmethod
    def _left_button():
        mouse = getattr(interception, "mouse", None) if interception is not None else None
        return getattr(mouse, "left", "left")

    @staticmethod
    def _call_interception(names, *args):
        if interception is None:
            raise RuntimeError("interception-python is not installed")
        for name in names:
            func = getattr(interception, name, None)
            if not func:
                continue
            try:
                return func(*args)
            except TypeError:
                if args:
                    return func()
                raise
        raise AttributeError(f"interception-python is missing required function: {names[0]}")

    def sample_click_target(self, x, y, window_rect=None):
        noise = self.coordinate_noise_px
        if noise:
            sigma = max(0.1, noise / 2)
            x_offset = self._clamp(random.gauss(0, sigma), -noise, noise)
            y_offset = self._clamp(random.gauss(0, sigma), -noise, noise)
            sampled_x = int(round(x + x_offset))
            sampled_y = int(round(y + y_offset))
        else:
            sampled_x = int(round(x))
            sampled_y = int(round(y))

        if window_rect:
            sampled_x = self._clamp(sampled_x, int(window_rect.left), int(window_rect.left + window_rect.width))
            sampled_y = self._clamp(sampled_y, int(window_rect.top), int(window_rect.top + window_rect.height))

        return sampled_x, sampled_y

    def _move_hardware_to(self, x, y):
        self._call_interception(("move_to",), int(x), int(y))

    def _mouse_down(self):
        self._call_interception(("mouse_down",), self._left_button())

    def _mouse_up(self):
        self._call_interception(("mouse_up",), self._left_button())

    def _key_down(self, key):
        self._call_interception(("key_down", "keydown"), key)

    def _key_up(self, key):
        self._call_interception(("key_up", "keyup"), key)

    def hotkey(self, *keys, context=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context) or not self.check_backend() or not self.check_foreground(active_context):
            return False
        normalized_keys = []
        try:
            normalized_keys = [self._virtual_key(key) for key in keys]
            for key in normalized_keys:
                self._key_down(key)
            if not self.delay_policy.wait(self.delay_policy.key_hold_delay, active_context):
                return False
        except Exception as exc:
            print(colored(f"Error during hotkey '{'+'.join(map(str, keys))}': {exc}", "red"))
            return False
        finally:
            for key in reversed(normalized_keys):
                try:
                    self._key_up(key)
                except Exception:
                    pass
        return self.delay_policy.wait(self.delay_policy.click_settle_delay, active_context)

    def smooth_move_to(self, x, y, context=None, duration=None, window_rect=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context) or not self.check_backend() or not self.check_foreground(active_context):
            return False
        if window_rect and not self.validate_bounds(x, y, window_rect):
            print(colored(f"Move blocked: ({x}, {y}) is outside the target window.", "red"))
            return False

        duration = self.move_duration if duration is None else max(0.0, float(duration))
        start_x, start_y = self._mouse_position()
        target_x = int(round(x))
        target_y = int(round(y))

        if duration <= 0:
            try:
                self._move_hardware_to(target_x, target_y)
            except Exception as exc:
                print(colored(f"Error during hardware move: {exc}", "red"))
                return False
            return True

        distance = math.hypot(target_x - start_x, target_y - start_y)
        wobble = min(distance * 0.3, 100)
        ctrl1_x = start_x + random.uniform(-wobble, wobble)
        ctrl1_y = start_y + random.uniform(-wobble, wobble)
        ctrl2_x = target_x + random.uniform(-wobble, wobble)
        ctrl2_y = target_y + random.uniform(-wobble, wobble)
        steps = max(5, int(duration * self.move_steps_per_second))

        for step in range(1, steps + 1):
            if not self.check_interlock(active_context):
                return False
            t = step / steps
            next_x = int(round(self._calculate_bezier_point(start_x, ctrl1_x, ctrl2_x, target_x, t)))
            next_y = int(round(self._calculate_bezier_point(start_y, ctrl1_y, ctrl2_y, target_y, t)))
            try:
                self._move_hardware_to(next_x, next_y)
            except Exception as exc:
                print(colored(f"Error during hardware move: {exc}", "red"))
                return False
            if not self.delay_policy.wait(duration / steps, active_context):
                return False
        return True

    def click(self, x, y, window_rect=None, remember_position=True, context=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context) or not self.check_backend() or not self.check_foreground(active_context):
            return False
        if window_rect and not self.validate_bounds(x, y, window_rect):
            print(colored(f"Click blocked: ({x}, {y}) is outside the target window.", "red"))
            return False

        target_x, target_y = self.sample_click_target(x, y, window_rect)
        if not self.smooth_move_to(target_x, target_y, active_context, window_rect=window_rect):
            return False

        mouse_is_down = False
        try:
            self._mouse_down()
            mouse_is_down = True
            if not self.delay_policy.wait(random.uniform(0.04, 0.12), active_context):
                return False
            self._mouse_up()
            mouse_is_down = False
            return self.delay_policy.wait(self.delay_policy.click_settle_delay, active_context)
        except Exception as exc:
            print(colored(f"Error during click execution: {exc}", "red"))
            return False
        finally:
            if mouse_is_down:
                try:
                    self._mouse_up()
                except Exception:
                    pass

    def move_to(self, x, y, window_rect=None, remember_position=False, context=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context):
            return False
        if window_rect and not self.validate_bounds(x, y, window_rect):
            print(colored(f"Move blocked: ({x}, {y}) is outside the target window.", "red"))
            return False

        try:
            return self.smooth_move_to(x, y, active_context, window_rect=window_rect)
        except Exception as exc:
            print(colored(f"Error during move execution: {exc}", "red"))
            return False

    def key_press(self, key, hold_seconds=None, presses=1, context=None):
        active_context = self._context(context)
        if not self.check_backend() or not self.check_foreground(active_context):
            return False
        hold_seconds = self.delay_policy.key_hold_delay if hold_seconds is None else hold_seconds
        normalized_key = self._virtual_key(key)

        for _ in range(presses):
            if not self.check_interlock(active_context):
                return False
            key_is_down = False
            try:
                self._key_down(normalized_key)
                key_is_down = True
                if not self.delay_policy.wait(hold_seconds, active_context):
                    return False
                self._key_up(normalized_key)
                key_is_down = False
            except Exception as exc:
                print(colored(f"Error during key press '{key}': {exc}", "red"))
                return False
            finally:
                if key_is_down:
                    try:
                        self._key_up(normalized_key)
                    except Exception:
                        pass
        return True

    def scroll(self, y_scroll=0, context=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context) or not self.check_backend() or not self.check_foreground(active_context):
            return False
        direction = -1 if y_scroll >= 0 else 1

        for _ in range(abs(y_scroll)):
            if not self.check_interlock(active_context):
                return False
            try:
                try:
                    self._call_interception(("scroll",), direction)
                except TypeError:
                    self._call_interception(("scroll",), 0, direction)
            except Exception as exc:
                print(colored(f"Error during scroll execution: {exc}", "red"))
                return False
            if not self.delay_policy.wait(self.delay_policy.scroll_settle_delay, active_context):
                return False
        return True
