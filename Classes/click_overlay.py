"""Transparent click-target overlay drawn on top of the game window.

Shows a crosshair, bounding box, and label at the proposed click coordinates.
Colour-coded by confidence: green (>0.9), yellow (0.7–0.9), red (<0.7).
"""


from logging_config import get_logger
from PyQt5 import QtCore, QtGui, QtWidgets

LOGGER = get_logger(__name__)


# Confidence thresholds for colour coding.
_HIGH_CONFIDENCE = 0.90
_MEDIUM_CONFIDENCE = 0.70

_GREEN = QtGui.QColor(0, 230, 118, 200)
_YELLOW = QtGui.QColor(255, 193, 7, 200)
_RED = QtGui.QColor(244, 67, 54, 200)
_CROSSHAIR_SIZE = 24
_BOX_PADDING = 6


def _confidence_colour(confidence):
    """Return a QColor based on confidence level."""
    if confidence >= _HIGH_CONFIDENCE:
        return _GREEN
    if confidence >= _MEDIUM_CONFIDENCE:
        return _YELLOW
    return _RED


class ClickOverlay(QtWidgets.QWidget):
    """Frameless, transparent widget that highlights the proposed click target.

    Usage::

        overlay = ClickOverlay()
        overlay.show_target(absolute_x, absolute_y, label, confidence, window_rect)
        # ... after approval or rejection ...
        overlay.dismiss()
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowTransparentForInput
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)

        self._target_x = 0
        self._target_y = 0
        self._label = ""
        self._confidence = 0.0
        self._colour = _GREEN
        self._visible = False
        self._action_type = "click"

        # Auto-dismiss timer (safety net — hides after 30s if not dismissed).
        self._auto_hide_timer = QtCore.QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.dismiss)

    def show_target(self, absolute_x, absolute_y, label, confidence,
                    window_rect=None, action_type="click"):
        """Position the overlay and draw the crosshair at the target.

        Args:
            absolute_x: Absolute screen X of the proposed click.
            absolute_y: Absolute screen Y of the proposed click.
            label: Human-readable target label.
            confidence: Model confidence 0.0–1.0.
            window_rect: Optional dict/object with left, top, width, height.
            action_type: Action type string for display.
        """
        if window_rect:
            left = int(getattr(window_rect, "left", 0) if hasattr(window_rect, "left") else window_rect.get("left", 0))
            top = int(getattr(window_rect, "top", 0) if hasattr(window_rect, "top") else window_rect.get("top", 0))
            width = int(getattr(window_rect, "width", 800) if hasattr(window_rect, "width") else window_rect.get("width", 800))
            height = int(getattr(window_rect, "height", 600) if hasattr(window_rect, "height") else window_rect.get("height", 600))
            self.setGeometry(left, top, width, height)
        else:
            # Fallback: centre on the target with a reasonable area.
            self.setGeometry(
                max(0, absolute_x - 200),
                max(0, absolute_y - 200),
                400, 400,
            )

        self._target_x = absolute_x - self.x()
        self._target_y = absolute_y - self.y()
        self._label = str(label or "target")
        self._confidence = float(confidence)
        self._colour = _confidence_colour(self._confidence)
        self._action_type = str(action_type or "click")
        self._visible = True

        self._auto_hide_timer.start(30_000)
        self.show()
        self.raise_()
        self.update()

    def dismiss(self):
        """Hide the overlay."""
        self._visible = False
        self._auto_hide_timer.stop()
        self.hide()

    def paintEvent(self, event):
        """Draw the crosshair, bounding box, and label."""
        if not self._visible:
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        x = self._target_x
        y = self._target_y
        colour = self._colour

        # Draw semi-transparent background circle.
        painter.setBrush(QtGui.QColor(colour.red(), colour.green(), colour.blue(), 40))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(QtCore.QPoint(x, y), _CROSSHAIR_SIZE + 12, _CROSSHAIR_SIZE + 12)

        # Draw crosshair lines.
        pen = QtGui.QPen(colour, 2.5, QtCore.Qt.SolidLine)
        painter.setPen(pen)
        painter.drawLine(x - _CROSSHAIR_SIZE, y, x + _CROSSHAIR_SIZE, y)
        painter.drawLine(x, y - _CROSSHAIR_SIZE, x, y + _CROSSHAIR_SIZE)

        # Draw centre dot.
        painter.setBrush(colour)
        painter.drawEllipse(QtCore.QPoint(x, y), 4, 4)

        # Draw bounding box (rounded rect).
        box_pen = QtGui.QPen(colour, 2.0, QtCore.Qt.DashLine)
        painter.setPen(box_pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        box_size = _CROSSHAIR_SIZE + _BOX_PADDING
        painter.drawRoundedRect(
            x - box_size, y - box_size,
            box_size * 2, box_size * 2,
            6, 6,
        )

        # Draw label and confidence text.
        font = QtGui.QFont("Segoe UI", 10, QtGui.QFont.Bold)
        painter.setFont(font)

        action_prefix = ""
        if self._action_type != "click":
            action_prefix = f"[{self._action_type}] "
        text = f"{action_prefix}{self._label}  {self._confidence:.0%}"

        # Background for text readability.
        metrics = QtGui.QFontMetrics(font)
        text_rect = metrics.boundingRect(text)
        text_x = x - text_rect.width() // 2
        text_y = y + box_size + 20

        # Clamp text inside the widget bounds.
        text_x = max(4, min(self.width() - text_rect.width() - 4, text_x))
        text_y = max(text_rect.height() + 4, min(self.height() - 4, text_y))

        bg_rect = QtCore.QRect(
            text_x - 4, text_y - text_rect.height() - 2,
            text_rect.width() + 8, text_rect.height() + 6,
        )
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(30, 30, 30, 180))
        painter.drawRoundedRect(bg_rect, 4, 4)

        painter.setPen(colour)
        painter.drawText(text_x, text_y, text)

        painter.end()
