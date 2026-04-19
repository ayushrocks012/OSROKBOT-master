"""Planner approval overlays shown on top of the game client.

This module owns two related UI-only surfaces:

- ``ClickOverlay`` renders detector boxes, the selected planner target, and an
  intent tooltip without blocking game input.
- ``PlannerCorrectionOverlay`` blocks clicks over the game client and captures
  a corrected normalized point for the planner ``Fix`` flow.
"""

from __future__ import annotations

from typing import Any
import textwrap

from logging_config import get_logger
from PyQt5 import QtCore, QtGui, QtWidgets

LOGGER = get_logger(__name__)

_HIGH_CONFIDENCE = 0.85
_MEDIUM_CONFIDENCE = 0.70
_CROSSHAIR_SIZE = 26
_BOX_PADDING = 8
_PREVIEW_BLUE = QtGui.QColor(86, 156, 214, 170)
_GREEN = QtGui.QColor(52, 211, 153, 220)
_YELLOW = QtGui.QColor(251, 191, 36, 220)
_RED = QtGui.QColor(248, 113, 113, 220)
_DARK_TOOLTIP = "background: rgba(9, 14, 23, 228); border: 1px solid rgba(120, 148, 188, 140); border-radius: 12px; color: #f8fafc; padding: 8px 12px;"


def _confidence_colour(confidence: float) -> QtGui.QColor:
    """Return a QColor for the supplied confidence."""

    if confidence >= _HIGH_CONFIDENCE:
        return _GREEN
    if confidence >= _MEDIUM_CONFIDENCE:
        return _YELLOW
    return _RED


def _coerce_window_rect(window_rect: object | None) -> QtCore.QRect:
    """Convert a dict-like or object-like client rect into a QRect."""

    if window_rect is None:
        return QtCore.QRect()
    left = int(getattr(window_rect, "left", 0) if hasattr(window_rect, "left") else window_rect.get("left", 0))
    top = int(getattr(window_rect, "top", 0) if hasattr(window_rect, "top") else window_rect.get("top", 0))
    width = int(getattr(window_rect, "width", 0) if hasattr(window_rect, "width") else window_rect.get("width", 0))
    height = int(getattr(window_rect, "height", 0) if hasattr(window_rect, "height") else window_rect.get("height", 0))
    return QtCore.QRect(left, top, max(0, width), max(0, height))


class ClickOverlay(QtWidgets.QWidget):
    """Preview a planner pointer target over the game window."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
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
        self._target_id = ""
        self._detections: list[dict[str, Any]] = []
        self._shortcut_hint = "Waiting for approval"

        self._intent_tooltip = QtWidgets.QLabel(self)
        self._intent_tooltip.setObjectName("plannerIntentTooltip")
        self._intent_tooltip.setStyleSheet(_DARK_TOOLTIP)
        self._intent_tooltip.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._intent_tooltip.setWordWrap(False)
        self._intent_tooltip.hide()

    @staticmethod
    def normalized_detection_rect(
        detection: dict[str, Any],
        width: int,
        height: int,
    ) -> QtCore.QRect | None:
        """Resolve one normalized detection payload into local widget coordinates."""

        try:
            center_x = float(detection.get("x", 0.0)) * width
            center_y = float(detection.get("y", 0.0)) * height
            box_width = max(0.0, float(detection.get("width", 0.0)) * width)
            box_height = max(0.0, float(detection.get("height", 0.0)) * height)
        except (TypeError, ValueError):
            return None
        if box_width <= 0 or box_height <= 0:
            return None
        left = int(round(center_x - box_width / 2.0))
        top = int(round(center_y - box_height / 2.0))
        return QtCore.QRect(left, top, int(round(box_width)), int(round(box_height)))

    def _selected_detection_rect(self) -> QtCore.QRect | None:
        for detection in self._detections:
            if str(detection.get("target_id", "")) != self._target_id:
                continue
            return self.normalized_detection_rect(detection, self.width(), self.height())
        return None

    def _tooltip_anchor_rect(self) -> QtCore.QRect:
        selected = self._selected_detection_rect()
        if selected is not None:
            return selected
        return QtCore.QRect(
            self._target_x - (_CROSSHAIR_SIZE + 6),
            self._target_y - (_CROSSHAIR_SIZE + 6),
            (_CROSSHAIR_SIZE + 6) * 2,
            (_CROSSHAIR_SIZE + 6) * 2,
        )

    def _update_intent_tooltip(self) -> None:
        if not self._visible:
            self._intent_tooltip.hide()
            return

        tooltip_text = f"<b>Action:</b> {self._action_type.title()} | <b>Conf:</b> {self._confidence:.0%}<br>"
        if self._sub_goal:
            tooltip_text += f"<span style='color: #94a3b8;'>Goal:</span> {self._sub_goal}<br>"
        if self._thought_process:
            tooltip_text += f"<span style='color: #94a3b8;'>Reason:</span> {self._thought_process}<br>"
        tooltip_text += f"<br><i>{self._shortcut_hint}</i>"
        
        self._intent_tooltip.setText(tooltip_text)
        self._intent_tooltip.adjustSize()
        size = self._intent_tooltip.sizeHint() + QtCore.QSize(12, 12)
        anchor = self._tooltip_anchor_rect()

        x = anchor.right() + 12
        if x + size.width() > self.width() - 8:
            x = max(8, anchor.left() - size.width() - 12)

        y = max(8, min(self.height() - size.height() - 8, anchor.top() - 4))
        self._intent_tooltip.setGeometry(x, y, size.width(), size.height())
        self._intent_tooltip.show()
        self._intent_tooltip.raise_()

    def show_target(
        self,
        absolute_x: int,
        absolute_y: int,
        label: str,
        confidence: float,
        window_rect: object | None = None,
        action_type: str = "click",
        detections: list[dict[str, Any]] | None = None,
        target_id: str = "",
        shortcut_hint: str = "Waiting for approval",
        thought_process: str = "",
        sub_goal: str = "",
    ) -> None:
        """Show the planner preview over the supplied client rectangle."""

        rect = _coerce_window_rect(window_rect)
        if rect.width() > 0 and rect.height() > 0:
            self.setGeometry(rect)
        else:
            self.setGeometry(max(0, absolute_x - 220), max(0, absolute_y - 220), 440, 440)

        self._target_x = absolute_x - self.x()
        self._target_y = absolute_y - self.y()
        self._label = str(label or "target")
        self._confidence = float(confidence or 0.0)
        self._colour = _confidence_colour(self._confidence)
        self._action_type = str(action_type or "click")
        self._target_id = str(target_id or "")
        self._detections = list(detections or [])
        self._shortcut_hint = str(shortcut_hint or "Waiting for approval")
        self._thought_process = textwrap.shorten(thought_process, width=120, placeholder="...") if thought_process else ""
        self._sub_goal = textwrap.shorten(sub_goal, width=80, placeholder="...") if sub_goal else ""
        self._visible = True

        self.show()
        self.raise_()
        self._update_intent_tooltip()
        self.update()

    def dismiss(self) -> None:
        """Hide the planner preview."""

        self._visible = False
        self._detections = []
        self._target_id = ""
        self._intent_tooltip.hide()
        self.hide()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_intent_tooltip()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        """Draw detector boxes, the selected target, and the preview crosshair."""

        if not self._visible:
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        selected_rect = self._selected_detection_rect()
        for detection in self._detections:
            rect = self.normalized_detection_rect(detection, self.width(), self.height())
            if rect is None:
                continue

            selected = str(detection.get("target_id", "")) == self._target_id
            colour = self._colour if selected else _PREVIEW_BLUE
            painter.setPen(QtGui.QPen(colour, 2.5 if selected else 1.5))
            painter.setBrush(QtGui.QColor(colour.red(), colour.green(), colour.blue(), 28 if selected else 18))
            painter.drawRoundedRect(rect, 10, 10)

            box_label = str(detection.get("label", "")).strip()
            if box_label:
                font = QtGui.QFont("Segoe UI", 8, QtGui.QFont.Bold if selected else QtGui.QFont.DemiBold)
                painter.setFont(font)
                metrics = QtGui.QFontMetrics(font)
                label_rect = metrics.boundingRect(box_label)
                label_x = max(8, min(self.width() - label_rect.width() - 12, rect.left() + 6))
                label_y = max(label_rect.height() + 10, rect.top() - 6)
                background = QtCore.QRect(
                    label_x - 5,
                    label_y - label_rect.height() - 5,
                    label_rect.width() + 10,
                    label_rect.height() + 8,
                )
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(9, 14, 23, 205))
                painter.drawRoundedRect(background, 8, 8)
                painter.setPen(colour)
                painter.drawText(label_x, label_y, box_label)

        x = self._target_x
        y = self._target_y
        colour = self._colour
        halo_radius = _CROSSHAIR_SIZE + 16

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(colour.red(), colour.green(), colour.blue(), 44))
        painter.drawEllipse(QtCore.QPoint(x, y), halo_radius, halo_radius)

        painter.setPen(QtGui.QPen(colour, 2.6))
        painter.drawLine(x - _CROSSHAIR_SIZE, y, x + _CROSSHAIR_SIZE, y)
        painter.drawLine(x, y - _CROSSHAIR_SIZE, x, y + _CROSSHAIR_SIZE)
        painter.setBrush(colour)
        painter.drawEllipse(QtCore.QPoint(x, y), 4, 4)

        painter.setPen(QtGui.QPen(colour, 2.0, QtCore.Qt.DashLine))
        painter.setBrush(QtCore.Qt.NoBrush)
        if selected_rect is None:
            box_size = _CROSSHAIR_SIZE + _BOX_PADDING
            selected_rect = QtCore.QRect(
                x - box_size,
                y - box_size,
                box_size * 2,
                box_size * 2,
            )
        painter.drawRoundedRect(selected_rect, 10, 10)

        label_font = QtGui.QFont("Segoe UI", 10, QtGui.QFont.Bold)
        painter.setFont(label_font)
        text = f"{self._label}  {self._confidence:.0%}"
        metrics = QtGui.QFontMetrics(label_font)
        text_rect = metrics.boundingRect(text)
        text_x = max(8, min(self.width() - text_rect.width() - 8, x - text_rect.width() // 2))
        text_y = min(self.height() - 12, selected_rect.bottom() + text_rect.height() + 18)
        background = QtCore.QRect(
            text_x - 6,
            text_y - text_rect.height() - 5,
            text_rect.width() + 12,
            text_rect.height() + 10,
        )
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(9, 14, 23, 205))
        painter.drawRoundedRect(background, 10, 10)
        painter.setPen(colour)
        painter.drawText(text_x, text_y, text)

        if self._intent_tooltip.isVisible() and selected_rect is not None:
            tooltip_rect = self._intent_tooltip.geometry()
            start = QtCore.QPoint(selected_rect.right(), selected_rect.center().y())
            end = QtCore.QPoint(tooltip_rect.left(), tooltip_rect.center().y())
            if tooltip_rect.center().x() < selected_rect.center().x():
                start = QtCore.QPoint(selected_rect.left(), selected_rect.center().y())
                end = QtCore.QPoint(tooltip_rect.right(), tooltip_rect.center().y())
            painter.setPen(QtGui.QPen(QtGui.QColor(colour.red(), colour.green(), colour.blue(), 170), 1.6))
            painter.drawLine(start, end)

        painter.end()


class PlannerCorrectionOverlay(QtWidgets.QWidget):
    """Capture a corrected planner point over the game window."""

    point_selected = QtCore.pyqtSignal(dict)
    selection_cancelled = QtCore.pyqtSignal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.CrossCursor)

        self._window_rect = QtCore.QRect()
        self._cursor_pos = QtCore.QPoint(0, 0)
        self._prompt_text = "Click the correct target"
        self._active = False

        self._instruction_label = QtWidgets.QLabel(self)
        self._instruction_label.setStyleSheet(_DARK_TOOLTIP)
        self._instruction_label.setAlignment(QtCore.Qt.AlignCenter)
        self._instruction_label.hide()

    def capture_for_window(
        self,
        window_rect: object | None,
        prompt_text: str = "Click the correct target",
    ) -> bool:
        """Show the blocking correction overlay over the supplied game rect."""

        rect = _coerce_window_rect(window_rect)
        if rect.width() <= 0 or rect.height() <= 0:
            LOGGER.warning("Planner correction overlay requested without a valid window rect.")
            return False

        self._window_rect = rect
        self._cursor_pos = QtCore.QPoint(rect.width() // 2, rect.height() // 2)
        self._prompt_text = str(prompt_text or "Click the correct target")
        self._active = True
        self.setGeometry(rect)
        self._instruction_label.setText(f"{self._prompt_text}\nLeft click to confirm. Esc or right click to cancel.")
        self._instruction_label.adjustSize()
        width = min(rect.width() - 24, max(360, self._instruction_label.sizeHint().width() + 20))
        height = self._instruction_label.sizeHint().height() + 16
        self._instruction_label.setGeometry(max(12, (rect.width() - width) // 2), 16, width, height)
        self._instruction_label.show()

        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(QtCore.Qt.ActiveWindowFocusReason)
        try:
            self.grabMouse()
            self.grabKeyboard()
        except Exception as exc:
            LOGGER.debug("Planner correction overlay input grab skipped: %s", exc)
        self.update()
        return True

    def dismiss(self) -> None:
        """Hide the blocking correction overlay."""

        self._active = False
        self._instruction_label.hide()
        try:
            self.releaseMouse()
            self.releaseKeyboard()
        except Exception:
            pass
        self.hide()

    def _emit_selected_point(self, point: QtCore.QPoint) -> None:
        width = max(1, self.width())
        height = max(1, self.height())
        normalized = {
            "x": max(0.0, min(1.0, point.x() / width)),
            "y": max(0.0, min(1.0, point.y() / height)),
        }
        self.point_selected.emit(normalized)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._cursor_pos = event.pos()
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._active:
            return
        if event.button() == QtCore.Qt.LeftButton:
            point = event.pos()
            self.dismiss()
            self._emit_selected_point(point)
            return
        if event.button() == QtCore.Qt.RightButton:
            self.dismiss()
            self.selection_cancelled.emit()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.dismiss()
            self.selection_cancelled.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if not self._active:
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(18, 24, 38, 34))
        painter.drawRoundedRect(self.rect(), 0, 0)

        border_rect = self.rect().adjusted(2, 2, -2, -2)
        painter.setPen(QtGui.QPen(QtGui.QColor(96, 165, 250, 165), 2.0))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRoundedRect(border_rect, 14, 14)

        x = self._cursor_pos.x()
        y = self._cursor_pos.y()
        accent = QtGui.QColor(96, 165, 250, 215)
        painter.setPen(QtGui.QPen(accent, 1.4))
        painter.drawLine(0, y, self.width(), y)
        painter.drawLine(x, 0, x, self.height())
        painter.setBrush(QtGui.QColor(accent.red(), accent.green(), accent.blue(), 40))
        painter.drawEllipse(QtCore.QPoint(x, y), 22, 22)
        painter.setPen(QtGui.QPen(accent, 2.0))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(QtCore.QPoint(x, y), 10, 10)
        painter.end()
