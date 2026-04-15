import os
import re
import subprocess
import time
from enum import Enum
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps
from termcolor import colored

from config_manager import ConfigManager
from helpers import UIMap
from image_finder import ImageFinder
from input_controller import InputController
from window_handler import WindowHandler


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class GameState(str, Enum):
    CITY = "CITY"
    MAP = "MAP"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class GameStateMonitor:
    """Reusable game-state and resource checks for workflow preconditions."""

    BLOCKER_IMAGES = (
        PROJECT_ROOT / "Media" / "confirm.png",
        PROJECT_ROOT / "Media" / "escx.png",
    )
    MAP_MARKER_IMAGE = PROJECT_ROOT / "Media" / "ficon.png"
    OCR_CACHE_SECONDS = 30
    DEFAULT_BARBARIAN_AP_COST = 50

    def __init__(self, context=None, threshold=0.85):
        self.context = context
        tesseract_path = ConfigManager().get("TESSERACT_PATH")
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        self.window_handler = WindowHandler()
        self.image_finder = ImageFinder(threshold=threshold)
        self.input_controller = InputController(context=context)

    def _window_title(self):
        if self.context and getattr(self.context, "window_title", None):
            return self.context.window_title
        return "Rise of Kingdoms"

    def _screenshot(self):
        return self.window_handler.screenshot_window(self._window_title())

    def _extract_roi(self, screenshot, roi):
        width, height = screenshot.size
        x, y, roi_width, roi_height = roi
        left = int(round(width * x))
        upper = int(round(height * y))
        right = int(round(width * (x + roi_width)))
        lower = int(round(height * (y + roi_height)))
        return screenshot.crop((left, upper, right, lower))

    def _ocr_digits(self, image, invert=True):
        image = image.convert("L")
        width, height = image.size
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        image = image.resize((max(1, width * 5), max(1, height * 5)), resampling)
        image = image.point(lambda value: 0 if value < 145 else 255, "1")
        if invert:
            image = ImageOps.invert(image.convert("L"))
        return pytesseract.image_to_string(
            image,
            lang="eng",
            config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789/",
        ).strip()

    @staticmethod
    def _parse_fraction(text):
        match = re.search(r"(\d+)\s*/\s*(\d+)", text or "")
        if match:
            return int(match.group(1)), int(match.group(2))
        digits = re.findall(r"\d+", text or "")
        if len(digits) >= 2:
            return int(digits[0]), int(digits[1])
        return None

    @staticmethod
    def _parse_first_number(text):
        match = re.search(r"\d+", text or "")
        return int(match.group(0)) if match else None

    def _cache_get(self, key, max_age_seconds):
        if not self.context:
            return None

        value = getattr(self.context, key, None)
        timestamp = getattr(self.context, f"{key}_checked_at", None)
        if value is None or timestamp is None:
            return None
        if time.monotonic() - timestamp > max_age_seconds:
            return None
        return value

    def _cache_set(self, key, value):
        if not self.context:
            return
        timestamp = time.monotonic()
        setattr(self.context, key, value)
        setattr(self.context, f"{key}_checked_at", timestamp)
        self.context.extracted[key] = {"value": value, "timestamp": timestamp}

    def _find_image(self, image_path, screenshot, window_rect, roi):
        found, x, y, count = self.image_finder.find_image_coordinates(
            str(image_path),
            screenshot,
            window_rect,
            0,
            0,
            1,
            search_region=roi,
        )
        return found, x, y, count

    def current_state(self):
        screenshot, window_rect = self._screenshot()
        if screenshot is None or window_rect is None:
            return GameState.UNKNOWN

        for blocker_image in self.BLOCKER_IMAGES:
            found, _, _, _ = self._find_image(blocker_image, screenshot, window_rect, UIMap.CENTER_MODAL)
            if found:
                return GameState.BLOCKED

        found, _, _, _ = self._find_image(
            self.MAP_MARKER_IMAGE,
            screenshot,
            window_rect,
            UIMap.MAP_VIEW_MARKER,
        )
        if found:
            return GameState.MAP

        return GameState.CITY

    def save_diagnostic_screenshot(self, label="recovery"):
        screenshot, _ = self._screenshot()
        if screenshot is None:
            print(colored("Diagnostic screenshot skipped: screenshot unavailable.", "yellow"))
            return None
        return self.image_finder.save_screenshot(screenshot, label=label)

    def clear_blockers(self):
        screenshot, window_rect = self._screenshot()
        if screenshot is None or window_rect is None:
            return False

        cleared = False
        for blocker_image in self.BLOCKER_IMAGES:
            found, click_x, click_y, _ = self._find_image(
                blocker_image,
                screenshot,
                window_rect,
                UIMap.CENTER_MODAL,
            )
            if not found or click_x is None or click_y is None:
                continue

            print(colored(f"State monitor clearing blocker: {blocker_image.name}", "yellow"))
            if not self.input_controller.click(click_x, click_y, window_rect=window_rect, context=self.context):
                return False
            self.input_controller.wait(0.2, context=self.context)
            cleared = True
            screenshot, window_rect = self._screenshot()
            if screenshot is None or window_rect is None:
                return cleared

        return cleared

    def is_known_state(self):
        return self.current_state() in {GameState.CITY, GameState.MAP}

    def is_map_view(self):
        return self.current_state() == GameState.MAP

    def count_idle_march_slots(self, max_age_seconds=OCR_CACHE_SECONDS):
        cached = self._cache_get("idle_march_slots", max_age_seconds)
        if cached is not None:
            return cached

        screenshot, _ = self._screenshot()
        if screenshot is None:
            print(colored("March slot check skipped: screenshot unavailable.", "yellow"))
            return None

        try:
            text = self._ocr_digits(self._extract_roi(screenshot, UIMap.TOP_RIGHT_MARCH_SLOTS))
            fraction = self._parse_fraction(text)
            if not fraction:
                print(colored(f"March slot OCR unreadable: {text!r}", "yellow"))
                return None
            used, total = fraction
            idle = max(0, total - used)
            print(colored(f"March slots: used={used} total={total} idle={idle}", "cyan"))
            self._cache_set("idle_march_slots", idle)
            return idle
        except Exception as exc:
            print(colored(f"March slot OCR failed: {exc}", "yellow"))
            return None

    def has_idle_march_slots(self, required=1):
        idle_slots = self.count_idle_march_slots()
        if idle_slots is None:
            return True
        return idle_slots >= required

    def read_action_points(self, max_age_seconds=OCR_CACHE_SECONDS):
        cached = self._cache_get("action_points", max_age_seconds)
        if cached is not None:
            return cached

        screenshot, _ = self._screenshot()
        if screenshot is None:
            print(colored("AP check skipped: screenshot unavailable.", "yellow"))
            return None

        try:
            text = self._ocr_digits(self._extract_roi(screenshot, UIMap.TOP_ACTION_POINTS))
            action_points = self._parse_first_number(text)
            if action_points is None:
                print(colored(f"AP OCR unreadable: {text!r}", "yellow"))
                return None
            print(colored(f"Action points: {action_points}", "cyan"))
            self._cache_set("action_points", action_points)
            return action_points
        except Exception as exc:
            print(colored(f"AP OCR failed: {exc}", "yellow"))
            return None

    def has_action_points(self, required=DEFAULT_BARBARIAN_AP_COST):
        action_points = self.read_action_points()
        if action_points is None:
            return True
        return action_points >= required

    def restart_client(self):
        """Restart the game client through an explicit hook or configured path."""
        bot = getattr(self.context, "bot", None) if self.context else None
        restart_hook = getattr(bot, "restart_client", None)
        if callable(restart_hook):
            print(colored("Restarting client through bot restart hook.", "yellow"))
            return bool(restart_hook())

        client_path = ConfigManager().get("ROK_CLIENT_PATH")
        if not client_path:
            print(colored("Client restart skipped: ROK_CLIENT_PATH is not configured.", "yellow"))
            return False

        window = self.window_handler.get_window(self._window_title())
        if window:
            self.window_handler.activate_window(self._window_title())
            if not self.input_controller.hotkey("alt", "f4", context=self.context):
                return False
            self.input_controller.wait(3, context=self.context)

        try:
            subprocess.Popen([client_path], cwd=str(Path(client_path).parent))
        except Exception as exc:
            print(colored(f"Client restart failed: {exc}", "red"))
            return False

        self.input_controller.wait(8, context=self.context)
        return True
