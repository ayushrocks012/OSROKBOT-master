import ctypes
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

import pygetwindow as gw
from logging_config import get_logger
from PIL import Image

LOGGER = get_logger(__name__)
WINDOW_HANDLER_EXCEPTIONS = (
    AttributeError,
    ctypes.ArgumentError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

try:
    import win32con
    import win32gui
    import win32ui
except ImportError:
    win32con = None
    win32gui = None
    win32ui = None


@dataclass
class ClientRect:
    """Screen-space rectangle for the rendered client area."""

    hwnd: int
    left: int
    top: int
    width: int
    height: int


@contextmanager
def _window_dc(hwnd):
    window_dc = None
    try:
        window_dc = win32gui.GetWindowDC(hwnd)
        yield window_dc
    finally:
        if window_dc:
            win32gui.ReleaseDC(hwnd, window_dc)


@contextmanager
def _source_dc(window_dc):
    source_dc = None
    try:
        source_dc = win32ui.CreateDCFromHandle(window_dc)
        yield source_dc
    finally:
        if source_dc:
            source_dc.DeleteDC()


@contextmanager
def _compatible_dc(source_dc):
    memory_dc = None
    try:
        memory_dc = source_dc.CreateCompatibleDC()
        yield memory_dc
    finally:
        if memory_dc:
            memory_dc.DeleteDC()


@contextmanager
def _compatible_bitmap(source_dc, width, height):
    bitmap = None
    try:
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        yield bitmap
    finally:
        if bitmap:
            win32gui.DeleteObject(bitmap.GetHandle())


class _WindowCaptureBackend:
    name = "unknown"

    def capture_client_image(self, hwnd: int, client_rect: ClientRect) -> Image.Image | None:
        raise NotImplementedError


class _Win32WindowCaptureBackend(_WindowCaptureBackend):
    name = "win32_printwindow"

    def capture_client_image(self, hwnd: int, client_rect: ClientRect) -> Image.Image | None:
        """Capture the client area from the window render buffer.

        PrintWindow reads the target window instead of the desktop surface when
        the game exposes that buffer. Some Unity windows reject PrintWindow; in
        that case BitBlt is used as a compatibility fallback.
        """
        if not WindowHandler._win32_available():
            LOGGER.error("pywin32 is required for background window capture.")
            return None

        window_left, window_top, window_right, window_bottom = win32gui.GetWindowRect(hwnd)
        window_width = max(1, int(window_right - window_left))
        window_height = max(1, int(window_bottom - window_top))
        client_offset_x = max(0, int(client_rect.left - window_left))
        client_offset_y = max(0, int(client_rect.top - window_top))

        with (
            _window_dc(hwnd) as window_dc,
            _source_dc(window_dc) as source_dc,
            _compatible_dc(source_dc) as memory_dc,
            _compatible_bitmap(source_dc, window_width, window_height) as bitmap,
        ):
            memory_dc.SelectObject(bitmap)
            print_window = ctypes.windll.user32.PrintWindow
            # PW_RENDERFULLCONTENT improves captures for modern DWM-backed apps.
            rendered = print_window(hwnd, memory_dc.GetSafeHdc(), 0x00000002)
            if not rendered:
                rendered = print_window(hwnd, memory_dc.GetSafeHdc(), 0)
            if not rendered:
                LOGGER.warning("PrintWindow failed; falling back to BitBlt window capture.")
                capture_blt = getattr(win32con, "CAPTUREBLT", 0x40000000)
                try:
                    memory_dc.BitBlt(
                        (0, 0),
                        (window_width, window_height),
                        source_dc,
                        (0, 0),
                        win32con.SRCCOPY | capture_blt,
                    )
                    rendered = True
                except WINDOW_HANDLER_EXCEPTIONS as exc:
                    LOGGER.error(f"BitBlt fallback failed: {exc}")
                    rendered = False
            if not rendered:
                LOGGER.error("Window capture failed for the target game window.")
                return None

            bitmap_info = bitmap.GetInfo()
            bitmap_bits = bitmap.GetBitmapBits(True)
            image = Image.frombuffer(
                "RGB",
                (bitmap_info["bmWidth"], bitmap_info["bmHeight"]),
                bitmap_bits,
                "raw",
                "BGRX",
                0,
                1,
            )
            return image.crop(
                (
                    client_offset_x,
                    client_offset_y,
                    client_offset_x + client_rect.width,
                    client_offset_y + client_rect.height,
                )
            )


class WindowHandler:
    ASPECT_RATIO_16_9 = 16 / 9
    ASPECT_RATIO_EPSILON = 0.02

    def __init__(self, capture_backend: _WindowCaptureBackend | None = None) -> None:
        self._capture_backend = capture_backend or _Win32WindowCaptureBackend()

    def get_window(self, title: str) -> Any:
        windows = gw.getWindowsWithTitle(title)

        if not windows:
            LOGGER.error(f"No window found with title: {title}")
            return None
        return windows[0]

    def _get_client_rect(self, win: Any) -> ClientRect:
        hwnd = win._hWnd
        rect = wintypes.RECT()
        point = wintypes.POINT(0, 0)

        if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError()
        if not ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point)):
            raise ctypes.WinError()

        return ClientRect(
            hwnd=int(hwnd),
            left=int(point.x),
            top=int(point.y),
            width=int(rect.right - rect.left),
            height=int(rect.bottom - rect.top),
        )

    @staticmethod
    def _win32_available():
        return win32con is not None and win32gui is not None and win32ui is not None

    @staticmethod
    def _restore_no_activate(hwnd):
        if not WindowHandler._win32_available():
            return
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
                win32gui.SetWindowPos(
                    hwnd,
                    None,
                    0,
                    0,
                    0,
                    0,
                    win32con.SWP_NOMOVE
                    | win32con.SWP_NOSIZE
                    | win32con.SWP_NOZORDER
                    | win32con.SWP_NOACTIVATE,
                )
        except WINDOW_HANDLER_EXCEPTIONS as exc:
            LOGGER.warning("Unable to restore window handle %s: %s", hwnd, exc)

    def _print_window_client_image(self, hwnd: int, client_rect: ClientRect) -> Image.Image | None:
        return self._capture_backend.capture_client_image(hwnd, client_rect)

    def screenshot_window(self, title: str) -> tuple[Image.Image | None, ClientRect | None]:
        win = self.get_window(title)
        if not win:
            return None, None

        self._restore_no_activate(win._hWnd)
        client_rect = self._get_client_rect(win)
        if client_rect.width <= 0 or client_rect.height <= 0:
            LOGGER.error(f"Invalid client area for window: {title}")
            return None, None

        try:
            started_at = time.perf_counter()
            screenshot = self._print_window_client_image(win._hWnd, client_rect)
            duration_ms = (time.perf_counter() - started_at) * 1000.0
            LOGGER.debug(
                "Window capture backend=%s title=%s duration_ms=%.2f",
                getattr(self._capture_backend, "name", "unknown"),
                title,
                duration_ms,
            )
        except WINDOW_HANDLER_EXCEPTIONS as exc:
            LOGGER.error(f"Window capture failed for '{title}': {exc}")
            return None, None

        if screenshot is None:
            return None, None
        return screenshot.convert("RGB"), client_rect

    def get_client_window_rect(self, title: str) -> ClientRect | None:
        win = self.get_window(title)
        if not win:
            return None
        self._restore_no_activate(win._hWnd)
        try:
            return self._get_client_rect(win)
        except WINDOW_HANDLER_EXCEPTIONS as exc:
            LOGGER.error(f"Unable to read client area for '{title}': {exc}")
            return None

    def enforce_aspect_ratio(self, title: str = "Rise of Kingdoms") -> bool:
        win = self.get_window(title)
        if not win:
            return False

        width = int(win.width)
        height = int(win.height)
        if width <= 0 or height <= 0:
            return False

        current_ratio = width / height
        if abs(current_ratio - self.ASPECT_RATIO_16_9) <= self.ASPECT_RATIO_EPSILON:
            return True

        new_width = width
        new_height = max(1, int(round(new_width / self.ASPECT_RATIO_16_9)))
        try:
            win.resizeTo(new_width, new_height)
            LOGGER.info(f"Adjusted '{title}' to 16:9 window size: {new_width}x{new_height}")
            return True
        except WINDOW_HANDLER_EXCEPTIONS as exc:
            LOGGER.error(f"Failed to enforce 16:9 aspect ratio for '{title}': {exc}")
            return False

    def activate_window(self, title: str = "Rise of Kingdoms") -> None:
        try:
            win = self.get_window(title)
            if win:
                self._restore_no_activate(win._hWnd)
        except WINDOW_HANDLER_EXCEPTIONS as exc:
            if "Error code from Windows: 0" not in str(exc):
                LOGGER.error(f"Failed to prepare window '{title}': {exc}")
        return

    def ensure_foreground(self, title: str = "Rise of Kingdoms", wait_seconds: float = 0.5) -> bool:
        if not self._win32_available():
            LOGGER.error("pywin32 is required to enforce the foreground game window.")
            return False

        try:
            win = self.get_window(title)
            if not win:
                return False

            hwnd = int(win._hWnd)
            if win32gui.GetForegroundWindow() == hwnd:
                return True

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

            try:
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except WINDOW_HANDLER_EXCEPTIONS as exc:
                LOGGER.warning(f"Unable to foreground '{title}': {exc}")

            if wait_seconds and wait_seconds > 0:
                time.sleep(wait_seconds)

            if win32gui.GetForegroundWindow() == hwnd:
                return True

            LOGGER.error(f"Target game window is not foreground: {title}")
            return False
        except WINDOW_HANDLER_EXCEPTIONS as exc:
            LOGGER.error(f"Foreground check failed for '{title}': {exc}")
            return False
