import re

# Restart uses explicit configured executable path without shell=True.
import subprocess  # nosec B404
import time
from enum import Enum
from pathlib import Path

import pytesseract
from config_manager import ConfigManager
from diagnostic_screenshot import save_diagnostic_screenshot
from helpers import UIMap
from input_controller import InputController
from logging_config import get_logger
from PIL import Image, ImageOps
from window_handler import WindowHandler

LOGGER = get_logger(__name__)
OCR_READ_EXCEPTIONS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    getattr(pytesseract, "TesseractError", RuntimeError),
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class GameState(str, Enum):
    CITY = "CITY"
    MAP = "MAP"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class GameStateMonitor:
    """Reusable OCR and coarse state checks.

    This monitor uses the YOLO object detector and OCR to classify the
    current game screen into CITY, MAP, BLOCKED, or UNKNOWN states.
    Planner-level perception is still handled by the YOLO/VLM path.
    """

    OCR_CACHE_SECONDS = 30
    DEFAULT_BARBARIAN_AP_COST = 50
    DEFAULT_TESSERACT_TIMEOUT_SECONDS = 5.0

    # Labels whose presence signals the game is on the world MAP.
    MAP_LABELS = {
        "searchaction", "gatheraction", "attackaction", "marchaction",
        "smallmarchaction", "scoutaction", "rallyaction",
    }

    # Labels whose presence signals a modal/blocker is covering the screen.
    BLOCKER_LABELS = {"confirm", "escx", "captcha", "captchachest", "captcha_chest"}

    # Labels associated with city-level UI.
    CITY_LABELS = {
        "newtroopaction", "useaction", "trainaction", "upgradeaction",
        "buildaction",
    }

    def __init__(self, context=None, threshold=0.85):
        _ = threshold
        self.context = context
        tesseract_path = ConfigManager().get("TESSERACT_PATH")
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        self.window_handler = WindowHandler()
        self.input_controller = InputController(context=context)
        self._detector = None

    def _get_detector(self):
        if self._detector is None:
            from object_detector import create_detector
            self._detector = create_detector()
        return self._detector

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
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        image = image.resize((max(1, width * 5), max(1, height * 5)), resampling)
        image = image.point(lambda value: 0 if value < 145 else 255, "1")
        if invert:
            image = ImageOps.invert(image.convert("L"))
        return pytesseract.image_to_string(
            image,
            lang="eng",
            config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789/",
            timeout=self._tesseract_timeout(),
        ).strip()

    def _tesseract_timeout(self):
        try:
            configured = float(ConfigManager().get("TESSERACT_TIMEOUT_SECONDS", self.DEFAULT_TESSERACT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            configured = self.DEFAULT_TESSERACT_TIMEOUT_SECONDS
        return max(1.0, configured)

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

    def _detect_labels(self, screenshot):
        """Run the YOLO detector and return a set of lowercased label strings."""
        try:
            detections = self._get_detector().detect(screenshot)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("State monitor detector skipped: %s", exc)
            return set()
        return {
            str(getattr(det, "label", "")).lower().replace(" ", "_")
            for det in detections
            if getattr(det, "label", "")
        }

    def current_state(self):
        """Classify the current game screen using YOLO detector labels.

        Returns:
            GameState: BLOCKED if modal overlay detected, MAP if map-specific
            labels found, CITY if city-specific labels found, UNKNOWN otherwise.
        """
        screenshot, window_rect = self._screenshot()
        if screenshot is None or window_rect is None:
            return GameState.UNKNOWN

        labels = self._detect_labels(screenshot)
        if not labels:
            return GameState.UNKNOWN

        # Blocker labels take highest priority — a modal covers any state.
        if labels.intersection(self.BLOCKER_LABELS):
            LOGGER.info("State monitor: BLOCKED (labels: %s)",
                        labels.intersection(self.BLOCKER_LABELS))
            return GameState.BLOCKED

        # Map-specific labels indicate world-map view.
        if labels.intersection(self.MAP_LABELS):
            LOGGER.info("State monitor: MAP (labels: %s)",
                        labels.intersection(self.MAP_LABELS))
            return GameState.MAP

        # City-specific labels indicate city view.
        if labels.intersection(self.CITY_LABELS):
            LOGGER.info("State monitor: CITY (labels: %s)",
                        labels.intersection(self.CITY_LABELS))
            return GameState.CITY

        LOGGER.info("State monitor: UNKNOWN (detected labels: %s)", labels)
        return GameState.UNKNOWN

    def save_diagnostic_screenshot(self, label="recovery"):
        screenshot, _ = self._screenshot()
        if screenshot is None:
            LOGGER.warning("Diagnostic screenshot skipped: screenshot unavailable.")
            return None
        return save_diagnostic_screenshot(screenshot, label=label)

    def clear_blockers(self):
        """Attempt to dismiss detected modal overlays by pressing Escape.

        Returns:
            bool: True if a blocker was detected and an Escape was sent.
        """
        screenshot, _ = self._screenshot()
        if screenshot is None:
            return False

        labels = self._detect_labels(screenshot)
        blocker_labels = labels.intersection(self.BLOCKER_LABELS)
        if not blocker_labels:
            return False

        LOGGER.info("Clearing blocker (labels: %s)", blocker_labels)
        return self.input_controller.key_press(
            "escape", hold_seconds=0.1, context=self.context
        )

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
            LOGGER.warning("March slot check skipped: screenshot unavailable.")
            return None

        try:
            text = self._ocr_digits(self._extract_roi(screenshot, UIMap.TOP_RIGHT_MARCH_SLOTS))
            fraction = self._parse_fraction(text)
            if not fraction:
                LOGGER.warning(f"March slot OCR unreadable: {text!r}")
                return None
            used, total = fraction
            idle = max(0, total - used)
            LOGGER.info(f"March slots: used={used} total={total} idle={idle}")
            self._cache_set("idle_march_slots", idle)
            return idle
        except OCR_READ_EXCEPTIONS as exc:
            LOGGER.warning(f"March slot OCR failed: {exc}")
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
            LOGGER.warning("AP check skipped: screenshot unavailable.")
            return None

        try:
            text = self._ocr_digits(self._extract_roi(screenshot, UIMap.TOP_ACTION_POINTS))
            action_points = self._parse_first_number(text)
            if action_points is None:
                LOGGER.warning(f"AP OCR unreadable: {text!r}")
                return None
            LOGGER.info(f"Action points: {action_points}")
            self._cache_set("action_points", action_points)
            return action_points
        except OCR_READ_EXCEPTIONS as exc:
            LOGGER.warning(f"AP OCR failed: {exc}")
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
            LOGGER.warning("Restarting client through bot restart hook.")
            return bool(restart_hook())

        client_path = ConfigManager().get("ROK_CLIENT_PATH")
        if not client_path:
            LOGGER.warning("Client restart skipped: ROK_CLIENT_PATH is not configured.")
            return False

        window = self.window_handler.get_window(self._window_title())
        if window:
            self.window_handler.activate_window(self._window_title())
            if not self.input_controller.hotkey("alt", "f4", context=self.context):
                return False
            self.input_controller.wait(3, context=self.context)

        try:
            client_p = Path(client_path).resolve()
            if not client_p.is_absolute() or not client_p.is_file():
                LOGGER.error("Client path is invalid or non-absolute.")
                return False
            subprocess.Popen([str(client_p)], cwd=str(client_p.parent))  # nosec B603
        except (OSError, ValueError) as exc:
            LOGGER.error(f"Client restart failed: {exc}")
            return False

        self.input_controller.wait(8, context=self.context)
        return True
