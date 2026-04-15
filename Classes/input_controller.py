import math
import random
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import pyautogui
from termcolor import colored


class WindowRect(Protocol):
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

    def sample_click_target(self, x, y, window_rect=None):
        noise = self.coordinate_noise_px
        sampled_x = int(round(x + random.randint(-noise, noise))) if noise else int(round(x))
        sampled_y = int(round(y + random.randint(-noise, noise))) if noise else int(round(y))

        if window_rect:
            sampled_x = self._clamp(sampled_x, int(window_rect.left), int(window_rect.left + window_rect.width))
            sampled_y = self._clamp(sampled_y, int(window_rect.top), int(window_rect.top + window_rect.height))

        return sampled_x, sampled_y

    def smooth_move_to(self, x, y, context=None, duration=None, window_rect=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context):
            return False
        if window_rect and not self.validate_bounds(x, y, window_rect):
            print(colored(f"Move blocked: ({x}, {y}) is outside the target window.", "red"))
            return False

        duration = self.move_duration if duration is None else max(0.0, float(duration))
        start_x, start_y = pyautogui.position()
        if duration <= 0:
            pyautogui.moveTo(x, y)
            return True

        steps = max(2, int(duration * self.move_steps_per_second))
        for step in range(1, steps + 1):
            if not self.check_interlock(active_context):
                return False
            t = step / steps
            eased_t = 0.5 - 0.5 * math.cos(math.pi * t)
            next_x = int(round(start_x + (x - start_x) * eased_t))
            next_y = int(round(start_y + (y - start_y) * eased_t))
            pyautogui.moveTo(next_x, next_y)
            if not self.delay_policy.wait(duration / steps, active_context):
                return False
        return True

    def click(self, x, y, window_rect=None, remember_position=True, context=None):
        active_context = self._context(context)
        if not self.check_interlock(active_context):
            return False
        if window_rect and not self.validate_bounds(x, y, window_rect):
            print(colored(f"Click blocked: ({x}, {y}) is outside the target window.", "red"))
            return False

        try:
            prev_active_window = pyautogui.getActiveWindow()
        except Exception:
            prev_active_window = None

        prev_mouse_x, prev_mouse_y = pyautogui.position()
        target_x, target_y = self.sample_click_target(x, y, window_rect)

        try:
            if not self.smooth_move_to(target_x, target_y, active_context, window_rect=window_rect):
                return False
            pyautogui.click(target_x, target_y)
            self.delay_policy.wait(self.delay_policy.click_settle_delay, active_context)
            if prev_active_window:
                prev_active_window.activate()
            if remember_position:
                self.smooth_move_to(prev_mouse_x, prev_mouse_y, active_context, window_rect=None)
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
            prev_active_window = pyautogui.getActiveWindow()
        except Exception:
            prev_active_window = None

        prev_mouse_x, prev_mouse_y = pyautogui.position()

        try:
            if not self.smooth_move_to(x, y, active_context, window_rect=window_rect):
                return False
            if prev_active_window:
                prev_active_window.activate()
            if remember_position:
                self.smooth_move_to(prev_mouse_x, prev_mouse_y, active_context, window_rect=None)
        except Exception as exc:
            print(colored(f"Error during move execution: {exc}", "red"))
            return False
        return True

    def key_press(self, key, hold_seconds=None, presses=1, context=None):
        active_context = self._context(context)
        hold_seconds = self.delay_policy.key_hold_delay if hold_seconds is None else hold_seconds
        for _ in range(presses):
            if not self.check_interlock(active_context):
                return False
            try:
                pyautogui.keyDown(key)
                if not self.delay_policy.wait(hold_seconds, active_context):
                    pyautogui.keyUp(key)
                    return False
                pyautogui.keyUp(key)
            except Exception as exc:
                print(colored(f"Error during key press '{key}': {exc}", "red"))
                return False
        return True

    def scroll(self, y_scroll=0, context=None):
        active_context = self._context(context)
        direction = -1 if y_scroll >= 0 else 1
        for _ in range(abs(y_scroll)):
            if not self.check_interlock(active_context):
                return False
            try:
                pyautogui.scroll(direction)
            except Exception as exc:
                print(colored(f"Error during scroll execution: {exc}", "red"))
                return False
            if not self.delay_policy.wait(self.delay_policy.scroll_settle_delay, active_context):
                return False
        return True
