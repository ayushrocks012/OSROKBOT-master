import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image
from mss import mss
import pygetwindow as gw


@dataclass
class ClientRect:
    """Screen-space rectangle for the rendered client area."""

    left: int
    top: int
    width: int
    height: int


class WindowHandler:
    ASPECT_RATIO_16_9 = 16 / 9
    ASPECT_RATIO_EPSILON = 0.02

    def get_window(self, title):
        windows = gw.getWindowsWithTitle(title)

        if not windows:
            print(f"No window found with title: {title}")
            return None
        return windows[0]

    def _get_client_rect(self, win):
        hwnd = win._hWnd
        rect = wintypes.RECT()
        point = wintypes.POINT(0, 0)

        if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError()
        if not ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point)):
            raise ctypes.WinError()

        return ClientRect(
            left=int(point.x),
            top=int(point.y),
            width=int(rect.right - rect.left),
            height=int(rect.bottom - rect.top),
        )

    def screenshot_window(self, title):
        win = self.get_window(title)
        if not win:
            return None, None

        client_rect = self._get_client_rect(win)
        if client_rect.width <= 0 or client_rect.height <= 0:
            print(f"Invalid client area for window: {title}")
            return None, None

        monitor = {
            "top": client_rect.top,
            "left": client_rect.left,
            "width": client_rect.width,
            "height": client_rect.height,
        }
        sct = mss()
        try:
            img = sct.grab(monitor)
        finally:
            sct.close()
        screenshot = Image.frombytes("RGB", img.size, img.rgb, "raw")
        return screenshot, client_rect

    def enforce_aspect_ratio(self, title="Rise of Kingdoms"):
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
            print(f"Adjusted '{title}' to 16:9 window size: {new_width}x{new_height}")
            return True
        except Exception as exc:
            print(f"Failed to enforce 16:9 aspect ratio for '{title}': {exc}")
            return False

    def activate_window(self, title="Rise of Kingdoms"):
        try:
            win = self.get_window(title)
            if win:
                if win.isMinimized:
                    win.restore()
                win.activate()
                self.enforce_aspect_ratio(title)
        except Exception as e:
            if "Error code from Windows: 0" not in str(e):
                print(f"Failed to activate window '{title}': {e}")
        return
