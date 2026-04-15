import math
import random
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from termcolor import colored

try:
    import win32api
    import win32con
    import win32gui
except ImportError:
    win32api = None
    win32con = None
    win32gui = None


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


class InputController:
    KEY_ALIASES = {
        "escape": 0x1B,
        "esc": 0x1B,
        "space": 0x20,
        "enter": 0x0D,
        "return": 0x0D,
        "tab": 0x09,
        "backspace": 0x08,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "shift": 0x10,
        "ctrl": 0x11,
        "control": 0x11,
        "alt": 0x12,
    }

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
    def _win32_available():
        return win32api is not None and win32con is not None and win32gui is not None

    @staticmethod
    def _pack_coordinates(x, y):
        return (int(y) & 0xFFFF) << 16 | (int(x) & 0xFFFF)

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
                return 0x70 + number - 1
        if len(normalized) == 1:
            if win32api is not None:
                vk = win32api.VkKeyScan(normalized)
                if vk != -1:
                    return vk & 0xFF
            return ord(normalized.upper())
        raise ValueError(f"Unsupported key for virtual input: {key}")

    @staticmethod
    def _key_lparam(vk, is_key_up=False):
        scan_code = win32api.MapVirtualKey(vk, 0) if win32api is not None else 0
        lparam = 1 | (scan_code << 16)
        if is_key_up:
            lparam |= 0xC0000000
        return lparam

    def _target_hwnd(self, context=None, window_rect=None):
        if window_rect and getattr(window_rect, "hwnd", None):
            return int(window_rect.hwnd)
        if window_rect and getattr(window_rect, "_hWnd", None):
            return int(window_rect._hWnd)

        active_context = self._context(context)
        window_title = getattr(active_context, "window_title", None) if active_context else None
        if not window_title:
            return None

        from window_handler import WindowHandler

        window = WindowHandler().get_window(window_title)
        if not window:
            return None
        return int(window._hWnd)

    def _screen_to_client(self, x, y, window_rect):
        hwnd = self._target_hwnd(window_rect=window_rect)
        if hwnd and win32gui is not None:
            client_x, client_y = win32gui.ScreenToClient(hwnd, (int(x), int(y)))
        elif window_rect:
            client_x = int(x) - int(window_rect.left)
            client_y = int(y) - int(window_rect.top)
        else:
            client_x, client_y = int(x), int(y)

        if window_rect:
            client_x = self._clamp(client_x, 0, max(0, int(window_rect.width) - 1))
            client_y = self._clamp(client_y, 0, max(0, int(window_rect.height) - 1))
        return int(client_x), int(client_y)

    def _post_mouse_message(self, hwnd, message, client_x, client_y, wparam=0):
        if not self._win32_available():
            print(colored("pywin32 is required for virtual mouse input.", "red"))
            return False
        win32gui.PostMessage(hwnd, message, wparam, self._pack_coordinates(client_x, client_y))
        return True

    def _post_key_message(self, hwnd, vk, is_key_up=False, system_key=False):
        if not self._win32_available():
            print(colored("pywin32 is required for virtual keyboard input.", "red"))
            return False
        if system_key:
            message = win32con.WM_SYSKEYUP if is_key_up else win32con.WM_SYSKEYDOWN
        else:
            message = win32con.WM_KEYUP if is_key_up else win32con.WM_KEYDOWN
        win32gui.PostMessage(hwnd, message, vk, self._key_lparam(vk, is_key_up=is_key_up))
        return True

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

    def hotkey(self, *keys, context=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context):
            return False
        if not self._win32_available():
            print(colored("Hotkey blocked: pywin32 is unavailable.", "red"))
            return False
        hwnd = self._target_hwnd(active_context)
        if not hwnd:
            print(colored("Hotkey blocked: target window handle is unavailable.", "red"))
            return False
        try:
            virtual_keys = [self._virtual_key(key) for key in keys]
            system_key = 0x12 in virtual_keys
            for vk in virtual_keys:
                if not self._post_key_message(hwnd, vk, system_key=system_key):
                    return False
            if not self.delay_policy.wait(self.delay_policy.key_hold_delay, active_context):
                return False
            for vk in reversed(virtual_keys):
                if not self._post_key_message(hwnd, vk, is_key_up=True, system_key=system_key):
                    return False
        except Exception as exc:
            print(colored(f"Error during hotkey '{'+'.join(keys)}': {exc}", "red"))
            return False
        return self.delay_policy.wait(self.delay_policy.click_settle_delay, active_context)

    def smooth_move_to(self, x, y, context=None, duration=None, window_rect=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context):
            return False
        if not self._win32_available():
            print(colored("Move blocked: pywin32 is unavailable.", "red"))
            return False
        if window_rect and not self.validate_bounds(x, y, window_rect):
            print(colored(f"Move blocked: ({x}, {y}) is outside the target window.", "red"))
            return False

        hwnd = self._target_hwnd(active_context, window_rect)
        if not hwnd:
            print(colored("Move blocked: target window handle is unavailable.", "red"))
            return False

        duration = self.move_duration if duration is None else max(0.0, float(duration))
        last_position = None
        if active_context:
            last_position = active_context.extracted.get("_last_virtual_mouse_position")
        start_x, start_y = last_position or (x, y)

        if duration <= 0:
            client_x, client_y = self._screen_to_client(x, y, window_rect)
            if not self._post_mouse_message(hwnd, win32con.WM_MOUSEMOVE, client_x, client_y):
                return False
            if active_context:
                active_context.extracted["_last_virtual_mouse_position"] = (x, y)
            return True

        steps = max(2, int(duration * self.move_steps_per_second))
        for step in range(1, steps + 1):
            if not self.check_interlock(active_context):
                return False
            t = step / steps
            eased_t = 0.5 - 0.5 * math.cos(math.pi * t)
            next_x = int(round(start_x + (x - start_x) * eased_t))
            next_y = int(round(start_y + (y - start_y) * eased_t))
            client_x, client_y = self._screen_to_client(next_x, next_y, window_rect)
            if not self._post_mouse_message(hwnd, win32con.WM_MOUSEMOVE, client_x, client_y):
                return False
            if not self.delay_policy.wait(duration / steps, active_context):
                return False
        if active_context:
            active_context.extracted["_last_virtual_mouse_position"] = (x, y)
        return True

    def click(self, x, y, window_rect=None, remember_position=True, context=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context):
            return False
        if not self._win32_available():
            print(colored("Click blocked: pywin32 is unavailable.", "red"))
            return False
        if window_rect and not self.validate_bounds(x, y, window_rect):
            print(colored(f"Click blocked: ({x}, {y}) is outside the target window.", "red"))
            return False

        hwnd = self._target_hwnd(active_context, window_rect)
        if not hwnd:
            print(colored("Click blocked: target window handle is unavailable.", "red"))
            return False

        target_x, target_y = self.sample_click_target(x, y, window_rect)

        try:
            if not self.smooth_move_to(target_x, target_y, active_context, window_rect=window_rect):
                return False
            client_x, client_y = self._screen_to_client(target_x, target_y, window_rect)
            if not self._post_mouse_message(
                hwnd,
                win32con.WM_LBUTTONDOWN,
                client_x,
                client_y,
                win32con.MK_LBUTTON,
            ):
                return False
            if not self.delay_policy.wait(self.delay_policy.click_settle_delay, active_context):
                return False
            if not self._post_mouse_message(hwnd, win32con.WM_LBUTTONUP, client_x, client_y):
                return False
            self.delay_policy.wait(self.delay_policy.click_settle_delay, active_context)
        except Exception as exc:
            print(colored(f"Error during click execution: {exc}", "red"))
            return False
        return True

    def move_to(self, x, y, window_rect=None, remember_position=False, context=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context):
            return False
        if window_rect and not self.validate_bounds(x, y, window_rect):
            print(colored(f"Move blocked: ({x}, {y}) is outside the target window.", "red"))
            return False

        try:
            if not self.smooth_move_to(x, y, active_context, window_rect=window_rect):
                return False
        except Exception as exc:
            print(colored(f"Error during move execution: {exc}", "red"))
            return False
        return True

    def key_press(self, key, hold_seconds=None, presses=1, context=None):
        active_context = self._context(context)
        if not self._win32_available():
            print(colored("Key press blocked: pywin32 is unavailable.", "red"))
            return False
        hold_seconds = self.delay_policy.key_hold_delay if hold_seconds is None else hold_seconds
        hwnd = self._target_hwnd(active_context)
        if not hwnd:
            print(colored("Key press blocked: target window handle is unavailable.", "red"))
            return False

        for _ in range(presses):
            if not self.check_interlock(active_context):
                return False
            try:
                vk = self._virtual_key(key)
                system_key = vk == 0x12
                if not self._post_key_message(hwnd, vk, system_key=system_key):
                    return False
                if not self.delay_policy.wait(hold_seconds, active_context):
                    self._post_key_message(hwnd, vk, is_key_up=True, system_key=system_key)
                    return False
                if not self._post_key_message(hwnd, vk, is_key_up=True, system_key=system_key):
                    return False
            except Exception as exc:
                print(colored(f"Error during key press '{key}': {exc}", "red"))
                return False
        return True

    def scroll(self, y_scroll=0, context=None):
        active_context = self._context(context)
        if not self._win32_available():
            print(colored("Scroll blocked: pywin32 is unavailable.", "red"))
            return False
        direction = -1 if y_scroll >= 0 else 1
        hwnd = self._target_hwnd(active_context)
        if not hwnd:
            print(colored("Scroll blocked: target window handle is unavailable.", "red"))
            return False

        for _ in range(abs(y_scroll)):
            if not self.check_interlock(active_context):
                return False
            try:
                delta = 120 * direction
                wparam = (delta & 0xFFFF) << 16
                win32gui.PostMessage(hwnd, win32con.WM_MOUSEWHEEL, wparam, 0)
            except Exception as exc:
                print(colored(f"Error during scroll execution: {exc}", "red"))
                return False
            if not self.delay_policy.wait(self.delay_policy.scroll_settle_delay, active_context):
                return False
        return True
