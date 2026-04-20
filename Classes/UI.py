"""PyQt Agent Supervisor Console view for OSROKBOT.

This module owns widget composition, styling, overlay presentation, and view
event forwarding. Runtime-facing behavior such as session logging, planner
approval state, and YOLO warmup lives in ``UIController.py``.
"""

from __future__ import annotations

import os
import sys
import webbrowser
from functools import partial
from pathlib import Path

import pygetwindow as gw
from click_overlay import ClickOverlay, PlannerCorrectionOverlay
from config_manager import ConfigManager
from emergency_stop import EmergencyStop
from health_check import HealthCheckDialog
from logging_config import get_logger
from PyQt5 import QtCore, QtGui, QtWidgets
from runtime_composition import SupervisorRuntimeComposition
from UIController import DEFAULT_MISSION, SupervisorSnapshot, UIController
from window_handler import WindowHandler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGGER = get_logger(__name__)
EmergencyStop.start_once()

MODE_SIZES = {
    "compact": QtCore.QSize(500, 108),
    "approval": QtCore.QSize(500, 330),
    "command": QtCore.QSize(580, 620),
}


def asset_path(*parts: str) -> str:
    """Return an absolute project asset path as a string."""

    return str(PROJECT_ROOT.joinpath(*parts))


def _repolish(widget: QtWidgets.QWidget) -> None:
    """Re-apply QSS after updating dynamic properties."""

    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


WINDOW_QSS = """
QWidget#SupervisorRoot {
    background: transparent;
}
QFrame#SupervisorShell {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 rgba(15, 23, 42, 248),
        stop: 1 rgba(8, 12, 20, 244)
    );
    border: 1px solid rgba(72, 94, 124, 150);
    border-radius: 28px;
}
QLabel#StateDot {
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    border-radius: 17px;
    background: rgba(56, 189, 248, 26);
    color: #38bdf8;
    font-size: 18px;
    font-weight: 700;
    qproperty-alignment: AlignCenter;
}
QLabel#StateTitle {
    color: #f8fafc;
    font-size: 16px;
    font-weight: 600;
}
QLabel#StateSubtitle {
    color: #94a3b8;
    font-size: 11px;
}
QLabel#Badge {
    padding: 6px 12px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    color: #dbeafe;
    background: rgba(37, 99, 235, 54);
    border: 1px solid rgba(96, 165, 250, 80);
}
QLabel#Badge[tone="success"] {
    color: #dcfce7;
    background: rgba(22, 163, 74, 48);
    border-color: rgba(74, 222, 128, 96);
}
QLabel#Badge[tone="warning"] {
    color: #fef3c7;
    background: rgba(217, 119, 6, 48);
    border-color: rgba(251, 191, 36, 96);
}
QLabel#Badge[tone="danger"] {
    color: #fee2e2;
    background: rgba(220, 38, 38, 48);
    border-color: rgba(248, 113, 113, 96);
}
QLabel#Badge[tone="accent"] {
    color: #e0e7ff;
    background: rgba(124, 58, 237, 48);
    border-color: rgba(167, 139, 250, 96);
}
QToolButton#ActionTool,
QToolButton#WindowTool {
    border: 1px solid rgba(86, 105, 134, 130);
    border-radius: 12px;
    background: rgba(18, 24, 38, 210);
    color: #e2e8f0;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
    padding: 0;
}
QToolButton#ActionTool:hover,
QToolButton#WindowTool:hover {
    border-color: rgba(125, 211, 252, 170);
    background: rgba(24, 34, 52, 228);
}
QToolButton#ActionTool:disabled {
    color: rgba(148, 163, 184, 120);
    border-color: rgba(71, 85, 105, 70);
}
QToolButton#WindowTool {
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
}
QFrame#SectionCard,
QFrame#IntentCard,
QFrame#MetricCard {
    background: rgba(15, 23, 42, 222);
    border: 1px solid rgba(71, 85, 105, 138);
    border-radius: 18px;
}
QFrame#ErrorCard {
    background: rgba(127, 29, 29, 200);
    border: 1px solid rgba(239, 68, 68, 140);
    border-radius: 14px;
}
QLabel#SectionTitle {
    color: #f8fafc;
    font-size: 13px;
    font-weight: 600;
}
QLabel#HintText {
    color: #94a3b8;
    font-size: 11px;
}
QLabel#ErrorBannerText {
    color: #fecaca;
    font-size: 12px;
    font-weight: 500;
}
QLabel#IntentTitle {
    color: #e2e8f0;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.2px;
}
QLabel#IntentPrimary {
    color: #f8fafc;
    font-size: 18px;
    font-weight: 700;
}
QLabel#IntentSecondary {
    color: #cbd5e1;
    font-size: 12px;
}
QLabel#ReasonLabel {
    color: #dbe4f0;
    font-size: 12px;
    font-style: italic;
    line-height: 1.4;
}
QProgressBar#ConfidenceBar {
    background: rgba(30, 41, 59, 220);
    border: 1px solid rgba(71, 85, 105, 120);
    border-radius: 10px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
}
QProgressBar#ConfidenceBar::chunk {
    border-radius: 9px;
    background: #ef4444;
}
QProgressBar#ConfidenceBar[tone="success"]::chunk {
    background: #10b981;
}
QProgressBar#ConfidenceBar[tone="warning"]::chunk {
    background: #f59e0b;
}
QProgressBar#ConfidenceBar[tone="danger"]::chunk {
    background: #ef4444;
}
QPushButton#ApprovalOk,
QPushButton#ApprovalNo,
QPushButton#ApprovalFix {
    min-height: 42px;
    border-radius: 14px;
    font-size: 13px;
    font-weight: 700;
    border: 1px solid rgba(148, 163, 184, 80);
    color: #f8fafc;
}
QPushButton#ApprovalOk {
    background: rgba(37, 99, 235, 210);
    border-color: rgba(96, 165, 250, 140);
}
QPushButton#ApprovalNo {
    background: rgba(190, 24, 93, 210);
    border-color: rgba(244, 114, 182, 140);
}
QPushButton#ApprovalFix {
    background: rgba(180, 83, 9, 210);
    border-color: rgba(251, 191, 36, 140);
}
QPushButton#ApprovalOk:disabled {
    background: rgba(30, 41, 59, 200);
    border-color: rgba(71, 85, 105, 100);
    color: rgba(203, 213, 225, 120);
}
QComboBox,
QLineEdit,
QPlainTextEdit {
    background: rgba(15, 23, 42, 228);
    color: #f8fafc;
    border: 1px solid rgba(71, 85, 105, 138);
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 12px;
}
QComboBox:hover,
QLineEdit:hover,
QPlainTextEdit:hover,
QComboBox:focus,
QLineEdit:focus,
QPlainTextEdit:focus {
    border-color: rgba(125, 211, 252, 170);
}
QComboBox::drop-down {
    border: none;
    width: 28px;
}
QComboBox::down-arrow {
    image: url(__DOWN_ARROW_ICON__);
    width: 10px;
    height: 10px;
}
QCheckBox {
    color: #e2e8f0;
    font-size: 12px;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
}
QCheckBox::indicator:unchecked {
    border: 1px solid rgba(71, 85, 105, 138);
    border-radius: 6px;
    background: rgba(15, 23, 42, 228);
}
QCheckBox::indicator:checked {
    border: 1px solid rgba(96, 165, 250, 160);
    border-radius: 6px;
    background: rgba(37, 99, 235, 210);
}
QPushButton#AutonomyButton {
    min-height: 40px;
    border-radius: 14px;
    border: 1px solid rgba(71, 85, 105, 138);
    background: rgba(15, 23, 42, 228);
    color: #cbd5e1;
    font-size: 12px;
    font-weight: 600;
    padding: 0 12px;
}
QPushButton#AutonomyButton:hover {
    border-color: rgba(125, 211, 252, 170);
}
QPushButton#AutonomyButton[segment="l1"]:checked {
    background: rgba(37, 99, 235, 210);
    border-color: rgba(96, 165, 250, 160);
    color: #eff6ff;
}
QPushButton#AutonomyButton[segment="l2"]:checked {
    background: rgba(124, 58, 237, 210);
    border-color: rgba(167, 139, 250, 160);
    color: #f5f3ff;
}
QPushButton#AutonomyButton[segment="l3"]:checked {
    background: rgba(220, 38, 38, 210);
    border-color: rgba(248, 113, 113, 160);
    color: #fef2f2;
}
QTabWidget::pane {
    border: 1px solid rgba(71, 85, 105, 138);
    border-radius: 16px;
    top: -1px;
    background: rgba(10, 15, 25, 160);
}
QTabBar::tab {
    background: rgba(15, 23, 42, 210);
    border: 1px solid rgba(71, 85, 105, 110);
    color: #94a3b8;
    padding: 9px 18px;
    margin-right: 6px;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
    font-size: 12px;
    font-weight: 600;
}
QTabBar::tab:selected {
    color: #f8fafc;
    background: rgba(37, 99, 235, 170);
    border-color: rgba(96, 165, 250, 150);
}
QLabel#MetricTitle {
    color: #94a3b8;
    font-size: 11px;
    font-weight: 600;
}
QLabel#MetricValue {
    color: #f8fafc;
    font-size: 18px;
    font-weight: 700;
}
QListWidget {
    background: rgba(2, 6, 23, 160);
    border: 1px solid rgba(71, 85, 105, 138);
    border-radius: 14px;
    color: #e2e8f0;
    font-family: Consolas, "Courier New", monospace;
    font-size: 11px;
    padding: 6px;
}
QListWidget::item {
    padding: 6px 4px;
    border-bottom: 1px solid rgba(30, 41, 59, 120);
}
QPushButton#DialogPrimary,
QPushButton#DialogSecondary {
    min-height: 36px;
    border-radius: 12px;
    padding: 0 14px;
    font-size: 12px;
    font-weight: 600;
    color: #f8fafc;
    border: 1px solid rgba(96, 165, 250, 140);
}
QPushButton#DialogPrimary {
    background: rgba(37, 99, 235, 210);
}
QPushButton#DialogSecondary {
    background: rgba(15, 23, 42, 210);
    border-color: rgba(71, 85, 105, 138);
}
"""


class SettingsDialog(QtWidgets.QDialog):
    """Operator settings dialog for local paths and planner defaults."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = ConfigManager()
        self.setWindowTitle("OSROKBOT Settings")
        self.setMinimumWidth(560)
        self.setModal(True)
        self.setStyleSheet(parent.styleSheet() if parent else "")

        self.openai_key_input = QtWidgets.QLineEdit(self.config.get("OPENAI_KEY", "") or "")
        self.openai_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.tesseract_input = QtWidgets.QLineEdit(self.config.get("TESSERACT_PATH", "") or "")
        self.yolo_weights_input = QtWidgets.QLineEdit(self.config.get("ROK_YOLO_WEIGHTS", "") or "")
        self.yolo_url_input = QtWidgets.QLineEdit(self.config.get("ROK_YOLO_WEIGHTS_URL", "") or "")
        self.model_input = QtWidgets.QLineEdit(self.config.get("OPENAI_VISION_MODEL", "gpt-5.4-mini") or "gpt-5.4-mini")
        self.planner_goal_input = QtWidgets.QLineEdit(self.config.get("PLANNER_GOAL", DEFAULT_MISSION) or DEFAULT_MISSION)
        self.secret_provider_input = QtWidgets.QComboBox()
        self.secret_provider_input.addItem(".env (workstation)", "dotenv")
        if os.name == "nt":
            self.secret_provider_input.addItem("Windows DPAPI (encrypted)", "dpapi")
        current_provider = str(self.config.get("SECRET_PROVIDER", self.config.secret_provider_name) or self.config.secret_provider_name)
        provider_index = self.secret_provider_input.findData(current_provider)
        if provider_index >= 0:
            self.secret_provider_input.setCurrentIndex(provider_index)
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("HintText")

        # Create tab widget
        self.tabs = QtWidgets.QTabWidget()
        
        # --- General Tab ---
        general_tab = QtWidgets.QWidget()
        general_form = QtWidgets.QFormLayout(general_tab)
        general_form.setLabelAlignment(QtCore.Qt.AlignLeft)
        general_form.setFormAlignment(QtCore.Qt.AlignTop)
        general_form.setHorizontalSpacing(16)
        general_form.setVerticalSpacing(12)
        general_form.addRow("Secret Provider", self.secret_provider_input)
        general_form.addRow("OpenAI API Key", self.openai_key_input)
        
        # --- Planner AI Tab ---
        planner_tab = QtWidgets.QWidget()
        planner_form = QtWidgets.QFormLayout(planner_tab)
        planner_form.setLabelAlignment(QtCore.Qt.AlignLeft)
        planner_form.setFormAlignment(QtCore.Qt.AlignTop)
        planner_form.setHorizontalSpacing(16)
        planner_form.setVerticalSpacing(12)
        planner_form.addRow("OpenAI Model", self.model_input)
        planner_form.addRow("Default Mission", self.planner_goal_input)
        
        # --- Vision & OCR Tab ---
        vision_tab = QtWidgets.QWidget()
        vision_form = QtWidgets.QFormLayout(vision_tab)
        vision_form.setLabelAlignment(QtCore.Qt.AlignLeft)
        vision_form.setFormAlignment(QtCore.Qt.AlignTop)
        vision_form.setHorizontalSpacing(16)
        vision_form.setVerticalSpacing(12)
        vision_form.addRow("YOLO Weights URL", self.yolo_url_input)
        vision_form.addRow("YOLO Weights", self._path_row(self.yolo_weights_input, self.browse_yolo_weights))
        vision_form.addRow("Tesseract Path", self._path_row(self.tesseract_input, self.browse_tesseract))
        
        self.tabs.addTab(general_tab, "General")
        self.tabs.addTab(planner_tab, "Planner AI")
        self.tabs.addTab(vision_tab, "Vision & OCR")

        save_button = QtWidgets.QPushButton("Save")
        save_button.setObjectName("DialogPrimary")
        save_button.clicked.connect(self.save_settings)
        close_button = QtWidgets.QPushButton("Close")
        close_button.setObjectName("DialogSecondary")
        close_button.clicked.connect(self.reject)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(close_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(16)

        title = QtWidgets.QLabel("Runtime Settings")
        title.setObjectName("SectionTitle")
        subtitle = QtWidgets.QLabel("Secrets use the selected provider. Mission defaults and local paths stay in config.json.")
        subtitle.setObjectName("HintText")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.tabs)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

    def _path_row(self, line_edit: QtWidgets.QLineEdit, browse_handler: QtCore.pyqtBoundSignal | callable) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        browse_button = QtWidgets.QPushButton("Browse")
        browse_button.setObjectName("DialogSecondary")
        browse_button.clicked.connect(browse_handler)
        layout.addWidget(line_edit)
        layout.addWidget(browse_button)
        return row

    def browse_tesseract(self) -> None:
        """Prompt for the local `tesseract.exe` path and update the field."""

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select tesseract.exe",
            "",
            "Executable Files (*.exe);;All Files (*)",
        )
        if path:
            self.tesseract_input.setText(path)

    def browse_yolo_weights(self) -> None:
        """Prompt for the local YOLO weights file and update the field."""

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select YOLO weights",
            "",
            "YOLO Weights (*.pt);;All Files (*)",
        )
        if path:
            self.yolo_weights_input.setText(path)

    def save_settings(self) -> None:
        """Persist the editable runtime settings through the configured providers."""

        self.config.set_many(
            {
                "OPENAI_KEY": self.openai_key_input.text(),
                "SECRET_PROVIDER": self.secret_provider_input.currentData(),
                "TESSERACT_PATH": self.tesseract_input.text(),
                "ROK_YOLO_WEIGHTS": self.yolo_weights_input.text(),
                "ROK_YOLO_WEIGHTS_URL": self.yolo_url_input.text(),
                "OPENAI_VISION_MODEL": self.model_input.text(),
                "PLANNER_GOAL": self.planner_goal_input.text(),
            }
        )
        provider_label = self.secret_provider_input.currentText()
        self.status_label.setText(f"Saved via {provider_label}. YOLO warmup will refresh in the background.")
        self.accept()


class MetricCard(QtWidgets.QFrame):
    """Small dashboard card for one key session metric."""

    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)

        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("MetricTitle")
        self.value_label = QtWidgets.QLabel("--")
        self.value_label.setObjectName("MetricValue")
        self.value_label.setWordWrap(True)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addStretch()

    def set_value(self, value: str) -> None:
        """Update the dashboard card value text."""

        self.value_label.setText(value)


class IntentCard(QtWidgets.QFrame):
    """Approval card that surfaces planner action intent and confidence."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("IntentCard")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        top_row = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("Awaiting approval")
        self.title_label.setObjectName("IntentTitle")
        self.badge_label = QtWidgets.QLabel("0% confidence")
        self.badge_label.setObjectName("Badge")
        top_row.addWidget(self.title_label)
        top_row.addStretch()
        top_row.addWidget(self.badge_label)

        self.action_label = QtWidgets.QLabel("Click")
        self.action_label.setObjectName("IntentPrimary")
        self.target_label = QtWidgets.QLabel("target")
        self.target_label.setObjectName("IntentSecondary")

        self.confidence_bar = QtWidgets.QProgressBar()
        self.confidence_bar.setObjectName("ConfidenceBar")
        self.confidence_bar.setTextVisible(False)
        self.confidence_bar.setRange(0, 100)

        self.reason_label = QtWidgets.QLabel("No planner reason supplied.")
        self.reason_label.setObjectName("ReasonLabel")
        self.reason_label.setWordWrap(True)

        self.coordinates_label = QtWidgets.QLabel("")
        self.coordinates_label.setObjectName("HintText")
        self.shortcuts_label = QtWidgets.QLabel("")
        self.shortcuts_label.setObjectName("HintText")

        layout.addLayout(top_row)
        layout.addWidget(self.action_label)
        layout.addWidget(self.target_label)
        layout.addWidget(self.confidence_bar)
        layout.addWidget(self.reason_label)
        layout.addWidget(self.coordinates_label)
        layout.addWidget(self.shortcuts_label)

    def apply_state(self, state: object) -> None:
        """Render one approval-card state payload on the console."""

        if not hasattr(state, "visible") or not state.visible:
            self.title_label.setText("Awaiting approval")
            self.action_label.setText("No pending action")
            self.target_label.setText("")
            self.reason_label.setText("The planner is not currently waiting for input.")
            self.coordinates_label.setText("")
            self.shortcuts_label.setText("")
            self.badge_label.setText("0% confidence")
            self.badge_label.setProperty("tone", "info")
            _repolish(self.badge_label)
            self.confidence_bar.setProperty("tone", "danger")
            self.confidence_bar.setValue(0)
            _repolish(self.confidence_bar)
            return

        self.title_label.setText(state.title)
        self.action_label.setText(state.action_text)
        self.target_label.setText(state.target_text)
        self.reason_label.setText(state.reason_text)
        self.coordinates_label.setText(state.coordinates_text)
        self.shortcuts_label.setText(state.shortcut_hint)
        self.badge_label.setText(state.confidence_caption)
        self.badge_label.setProperty("tone", state.confidence_tone)
        _repolish(self.badge_label)
        self.confidence_bar.setProperty("tone", state.confidence_tone)
        self.confidence_bar.setValue(int(round(float(state.confidence) * 100.0)))
        _repolish(self.confidence_bar)


class AutonomySelector(QtWidgets.QWidget):
    """Segmented control for selecting autonomy level."""

    level_changed = QtCore.pyqtSignal(int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[int, QtWidgets.QPushButton] = {}
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        definitions = {
            1: ("L1 approve", "l1", "Pointer actions require review."),
            2: ("L2 trusted", "l2", "Trusted labels can auto-run."),
            3: ("L3 auto", "l3", "Validated pointer actions auto-execute."),
        }
        for level, (label, segment, tooltip) in definitions.items():
            button = QtWidgets.QPushButton(label)
            button.setObjectName("AutonomyButton")
            button.setProperty("segment", segment)
            button.setCheckable(True)
            button.setToolTip(tooltip)
            button.clicked.connect(partial(self.level_changed.emit, level))
            self._buttons[level] = button
            layout.addWidget(button)

    def set_level(self, level: int) -> None:
        """Update the segmented autonomy control to the selected level."""

        for value, button in self._buttons.items():
            button.blockSignals(True)
            button.setChecked(value == level)
            button.blockSignals(False)


class DashboardTab(QtWidgets.QWidget):
    """Dashboard tab showing session stats and timeline."""

    CARD_ORDER = [
        "Mission",
        "Autonomy",
        "Duration",
        "Actions",
        "Approvals",
        "Corrections",
        "API Calls",
        "Errors",
        "CAPTCHAs",
    ]

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: dict[str, MetricCard] = {}

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        for index, title in enumerate(self.CARD_ORDER):
            card = MetricCard(title)
            row, column = divmod(index, 3)
            grid.addWidget(card, row, column)
            self._cards[title] = card

        timeline_title = QtWidgets.QLabel("Timeline")
        timeline_title.setObjectName("SectionTitle")
        timeline_hint = QtWidgets.QLabel("Recent actions, approvals, corrections, and errors.")
        timeline_hint.setObjectName("HintText")
        self.timeline_list = QtWidgets.QListWidget()

        layout.addLayout(grid)
        layout.addWidget(timeline_title)
        layout.addWidget(timeline_hint)
        layout.addWidget(self.timeline_list, 1)

    def apply_snapshot(self, snapshot: SupervisorSnapshot) -> None:
        """Render one supervisor snapshot into the dashboard widgets."""

        for title in self.CARD_ORDER:
            self._cards[title].set_value(snapshot.dashboard_summary.get(title, "--"))

        self.timeline_list.blockSignals(True)
        self.timeline_list.clear()
        self.timeline_list.addItems(snapshot.timeline_lines)
        self.timeline_list.blockSignals(False)
        if self.timeline_list.count():
            self.timeline_list.scrollToBottom()


class UI(QtWidgets.QWidget):
    """PyQt view for the Agent Supervisor Console."""

    def __init__(self, window_title: str, delay: float = 0.0) -> None:
        super().__init__()
        os.chdir(PROJECT_ROOT)

        self.target_title = window_title
        self.runtime_composition = SupervisorRuntimeComposition(window_title, delay=delay)
        self.controller = UIController(
            window_title,
            delay=delay,
            composition=self.runtime_composition,
            parent=self,
        )
        self.OS_ROKBOT = self.controller.OS_ROKBOT
        self._click_overlay = ClickOverlay()
        self._correction_overlay = PlannerCorrectionOverlay()
        self._current_mode = ""
        self._last_window_lookup_at = 0.0
        self._window_lookup_interval_ms = 140
        self._cached_target_window = None
        self._cached_active_window = None
        self._last_stays_on_top: bool | None = None
        self._tray: QtWidgets.QSystemTrayIcon | None = None

        self.setObjectName("SupervisorRoot")
        self.setWindowTitle("OSROKBOT")
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setStyleSheet(
            WINDOW_QSS.replace(
                "__DOWN_ARROW_ICON__",
                asset_path("Media", "UI", "down_arrow.svg").replace("\\", "/"),
            )
        )

        self._setup_shell()
        self._setup_tray()
        self._setup_shortcuts()
        self._connect_controller()

        self.position_timer = QtCore.QTimer(self)
        self.position_timer.timeout.connect(self.update_position)
        self.position_timer.start(self._window_lookup_interval_ms)

        self.apply_snapshot(self.controller.snapshot())
        self.show()
        self.raise_()
        self.activateWindow()

    @property
    def current_context(self):
        """Return the controller's current runtime context."""

        return self.controller.current_context

    def _setup_shell(self) -> None:
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 10, 10, 10)

        self.shell = QtWidgets.QFrame()
        self.shell.setObjectName("SupervisorShell")
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.shell)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 10)
        shadow.setColor(QtGui.QColor(0, 0, 0, 125))
        self.shell.setGraphicsEffect(shadow)

        outer_layout.addWidget(self.shell)
        shell_layout = QtWidgets.QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(18, 16, 18, 18)
        shell_layout.setSpacing(14)

        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setSpacing(12)

        self.state_dot = QtWidgets.QLabel("○")
        self.state_dot.setObjectName("StateDot")

        self.state_title_label = QtWidgets.QLabel("Ready")
        self.state_title_label.setObjectName("StateTitle")
        self.state_subtitle_label = QtWidgets.QLabel("Standing by")
        self.state_subtitle_label.setObjectName("StateSubtitle")

        labels_layout = QtWidgets.QVBoxLayout()
        labels_layout.setContentsMargins(0, 0, 0, 0)
        labels_layout.setSpacing(2)
        labels_layout.addWidget(self.state_title_label)
        labels_layout.addWidget(self.state_subtitle_label)

        self.status_badge = QtWidgets.QLabel("Standing by")
        self.status_badge.setObjectName("Badge")
        self.elapsed_badge = QtWidgets.QLabel("00:00")
        self.elapsed_badge.setObjectName("Badge")

        self.start_button = self._tool_button(
            object_name="ActionTool",
            icon_path=asset_path("Media", "UI", "play_icon.svg"),
            tooltip="Start (F5)",
            slot=self.start_automation,
        )
        self.pause_icon = QtGui.QIcon(asset_path("Media", "UI", "pause_icon.svg"))
        self.play_icon = QtGui.QIcon(asset_path("Media", "UI", "play_icon.svg"))
        self.pause_button = self._tool_button(
            object_name="ActionTool",
            icon=self.pause_icon,
            tooltip="Pause (F6)",
            slot=self.toggle_pause,
        )
        self.stop_button = self._tool_button(
            object_name="ActionTool",
            icon_path=asset_path("Media", "UI", "stop_icon.svg"),
            tooltip="Stop (F7)",
            slot=self.stop_automation,
        )
        self.help_button = self._tool_button(
            object_name="WindowTool",
            text="?",
            tooltip="Open README",
            slot=self._open_help,
        )
        self.settings_button = self._tool_button(
            object_name="WindowTool",
            text="⚙",
            tooltip="Settings",
            slot=self.open_settings,
        )
        self.close_button = self._tool_button(
            object_name="WindowTool",
            text="×",
            tooltip="Close",
            slot=self.close,
        )

        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.setSpacing(8)
        controls_layout.addWidget(self.status_badge)
        controls_layout.addWidget(self.elapsed_badge)
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.pause_button)
        controls_layout.addWidget(self.stop_button)
        controls_layout.addWidget(self.help_button)
        controls_layout.addWidget(self.settings_button)
        controls_layout.addWidget(self.close_button)

        header_layout.addWidget(self.state_dot)
        header_layout.addLayout(labels_layout, 1)
        header_layout.addLayout(controls_layout)
        shell_layout.addLayout(header_layout)

        self.body_stack = QtWidgets.QStackedWidget()
        self.compact_page = QtWidgets.QWidget()
        self.approval_page = QtWidgets.QWidget()
        self.command_page = QtWidgets.QWidget()
        self.body_stack.addWidget(self.compact_page)
        self.body_stack.addWidget(self.approval_page)
        self.body_stack.addWidget(self.command_page)
        shell_layout.addWidget(self.body_stack)

        self._setup_approval_page()
        self._setup_command_page()

    def _tool_button(
        self,
        *,
        object_name: str,
        icon_path: str | None = None,
        icon: QtGui.QIcon | None = None,
        text: str = "",
        tooltip: str = "",
        slot: callable | None = None,
    ) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        button.setObjectName(object_name)
        if icon is not None:
            button.setIcon(icon)
            button.setIconSize(QtCore.QSize(17, 17))
        elif icon_path:
            button.setIcon(QtGui.QIcon(icon_path))
            button.setIconSize(QtCore.QSize(17, 17))
        if text:
            button.setText(text)
            button.setStyleSheet(button.styleSheet() + "font-size: 16px; font-weight: 700;")
        button.setToolTip(tooltip)
        if slot is not None:
            button.clicked.connect(slot)
        return button

    def _setup_approval_page(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.approval_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.intent_card = IntentCard()
        layout.addWidget(self.intent_card)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setSpacing(10)

        self.approve_button = QtWidgets.QPushButton("OK")
        self.approve_button.setObjectName("ApprovalOk")
        self.approve_button.setToolTip("Approve (F8)")
        self.approve_button.clicked.connect(self.approve_planner_action)

        self.reject_button = QtWidgets.QPushButton("No")
        self.reject_button.setObjectName("ApprovalNo")
        self.reject_button.setToolTip("Reject (F9)")
        self.reject_button.clicked.connect(self.reject_planner_action)

        self.correct_button = QtWidgets.QPushButton("Fix")
        self.correct_button.setObjectName("ApprovalFix")
        self.correct_button.setToolTip("Fix target (F10)")
        self.correct_button.clicked.connect(self.correct_planner_action)

        button_row.addWidget(self.approve_button)
        button_row.addWidget(self.reject_button)
        button_row.addWidget(self.correct_button)
        layout.addLayout(button_row)

    def _setup_command_page(self) -> None:
        layout = QtWidgets.QVBoxLayout(self.command_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)

        mission_tab = QtWidgets.QWidget()
        mission_layout = QtWidgets.QVBoxLayout(mission_tab)
        mission_layout.setContentsMargins(16, 16, 16, 16)
        mission_layout.setSpacing(16)

        mission_card = QtWidgets.QFrame()
        mission_card.setObjectName("SectionCard")
        mission_card_layout = QtWidgets.QVBoxLayout(mission_card)
        mission_card_layout.setContentsMargins(16, 16, 16, 16)
        mission_card_layout.setSpacing(10)

        mission_title = QtWidgets.QLabel("Mission Brief")
        mission_title.setObjectName("SectionTitle")
        mission_hint = QtWidgets.QLabel("Editable planner goal used when the next session starts.")
        mission_hint.setObjectName("HintText")

        self.mission_input = QtWidgets.QComboBox()
        self.mission_input.setEditable(True)
        self.mission_input.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.mission_input.lineEdit().setPlaceholderText("Type or select a mission...")

        mission_card_layout.addWidget(mission_title)
        mission_card_layout.addWidget(mission_hint)
        mission_card_layout.addWidget(self.mission_input)

        autonomy_card = QtWidgets.QFrame()
        autonomy_card.setObjectName("SectionCard")
        autonomy_layout = QtWidgets.QVBoxLayout(autonomy_card)
        autonomy_layout.setContentsMargins(16, 16, 16, 16)
        autonomy_layout.setSpacing(10)

        autonomy_title = QtWidgets.QLabel("Autonomy")
        autonomy_title.setObjectName("SectionTitle")
        autonomy_hint = QtWidgets.QLabel("L1 is the default supervised path. L2 and L3 collapse into the compact HUD while running.")
        autonomy_hint.setObjectName("HintText")

        self.autonomy_selector = AutonomySelector()
        self.autonomy_selector.level_changed.connect(self.controller.set_autonomy_level)

        autonomy_layout.addWidget(autonomy_title)
        autonomy_layout.addWidget(autonomy_hint)
        autonomy_layout.addWidget(self.autonomy_selector)

        operator_card = QtWidgets.QFrame()
        operator_card.setObjectName("SectionCard")
        operator_layout = QtWidgets.QVBoxLayout(operator_card)
        operator_layout.setContentsMargins(16, 16, 16, 16)
        operator_layout.setSpacing(8)

        operator_title = QtWidgets.QLabel("Operator Guidance")
        operator_title.setObjectName("SectionTitle")
        self.guidance_label = QtWidgets.QLabel("The console will expand automatically whenever a planner action needs approval.")
        self.guidance_label.setObjectName("HintText")
        self.guidance_label.setWordWrap(True)

        operator_layout.addWidget(operator_title)
        operator_layout.addWidget(self.guidance_label)

        teaching_card = QtWidgets.QFrame()
        teaching_card.setObjectName("SectionCard")
        teaching_layout = QtWidgets.QVBoxLayout(teaching_card)
        teaching_layout.setContentsMargins(16, 16, 16, 16)
        teaching_layout.setSpacing(10)

        teaching_title = QtWidgets.QLabel("Teaching Mode")
        teaching_title.setObjectName("SectionTitle")
        teaching_hint = QtWidgets.QLabel(
            "Use early supervised runs to teach gameplay doctrine. The selected profile and notes are fed into task decomposition and planning."
        )
        teaching_hint.setObjectName("HintText")
        teaching_hint.setWordWrap(True)

        self.teaching_mode_checkbox = QtWidgets.QCheckBox("Enable teaching mode for the next run")
        self.teaching_mode_checkbox.toggled.connect(self.controller.set_teaching_mode_enabled)

        self.teaching_profile_input = QtWidgets.QComboBox()
        self.teaching_profile_input.currentIndexChanged.connect(self._on_teaching_profile_changed)

        self.teaching_notes_input = QtWidgets.QPlainTextEdit()
        self.teaching_notes_input.setPlaceholderText(
            "Describe how you actually play this workflow. Example: from city press Space, then press F, choose Wood, click Gather, then Send."
        )
        self.teaching_notes_input.setMinimumHeight(110)
        self.teaching_notes_input.textChanged.connect(self._on_teaching_notes_changed)

        self.teaching_prompt_label = QtWidgets.QLabel("")
        self.teaching_prompt_label.setObjectName("HintText")
        self.teaching_prompt_label.setWordWrap(True)

        teaching_layout.addWidget(teaching_title)
        teaching_layout.addWidget(teaching_hint)
        teaching_layout.addWidget(self.teaching_mode_checkbox)
        teaching_layout.addWidget(self.teaching_profile_input)
        teaching_layout.addWidget(self.teaching_notes_input)
        teaching_layout.addWidget(self.teaching_prompt_label)

        self.error_card = QtWidgets.QFrame()
        self.error_card.setObjectName("ErrorCard")
        error_layout = QtWidgets.QVBoxLayout(self.error_card)
        error_layout.setContentsMargins(16, 16, 16, 16)
        error_layout.setSpacing(8)
        
        self.error_title = QtWidgets.QLabel("Error Guidance")
        self.error_title.setObjectName("SectionTitle")
        self.error_label = QtWidgets.QLabel("")
        self.error_label.setObjectName("ErrorBannerText")
        self.error_label.setWordWrap(True)
        
        error_layout.addWidget(self.error_title)
        error_layout.addWidget(self.error_label)
        self.error_card.setVisible(False)

        mission_layout.addWidget(self.error_card)
        mission_layout.addWidget(mission_card)
        mission_layout.addWidget(autonomy_card)
        mission_layout.addWidget(teaching_card)
        mission_layout.addWidget(operator_card)
        mission_layout.addStretch()

        self.dashboard_tab = DashboardTab()

        self.tabs.addTab(mission_tab, "Mission")
        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        layout.addWidget(self.tabs)

    def _setup_tray(self) -> None:
        try:
            icon = QtGui.QIcon(asset_path("Media", "UI", "play_icon.svg"))
            self._tray = QtWidgets.QSystemTrayIcon(icon, self)
            self._tray.setToolTip("OSROKBOT")
            self._tray.show()
        except Exception:
            self._tray = None

    def _notify(self, title: str, message: str, icon_type: object = QtWidgets.QSystemTrayIcon.Information) -> None:
        if self._tray and self._tray.supportsMessages():
            self._tray.showMessage(title, message, icon_type, 5000)

    def _setup_shortcuts(self) -> None:
        QtWidgets.QShortcut(QtGui.QKeySequence("F5"), self).activated.connect(self.start_automation)
        QtWidgets.QShortcut(QtGui.QKeySequence("F6"), self).activated.connect(self.toggle_pause)
        QtWidgets.QShortcut(QtGui.QKeySequence("F7"), self).activated.connect(self.stop_automation)
        QtWidgets.QShortcut(QtGui.QKeySequence("F8"), self).activated.connect(self.approve_planner_action)
        QtWidgets.QShortcut(QtGui.QKeySequence("F9"), self).activated.connect(self.reject_planner_action)
        QtWidgets.QShortcut(QtGui.QKeySequence("F10"), self).activated.connect(self.correct_planner_action)

    def _connect_controller(self) -> None:
        self.controller.snapshot_changed.connect(self.apply_snapshot)
        self.controller.planner_overlay_requested.connect(self._show_planner_overlay)
        self.controller.planner_overlay_cleared.connect(self._clear_planner_overlay)
        self.controller.fix_overlay_requested.connect(self._show_fix_overlay)
        self.controller.fix_overlay_cleared.connect(self._clear_fix_overlay)
        self.controller.notification_requested.connect(self._notify)
        self._correction_overlay.point_selected.connect(self.controller.apply_fix_selection)
        self._correction_overlay.selection_cancelled.connect(self.controller.cancel_fix_capture)

    def _sync_mission_input(self, snapshot: SupervisorSnapshot) -> None:
        if self.mission_input.hasFocus():
            return

        current_items = [self.mission_input.itemText(index) for index in range(self.mission_input.count())]
        if current_items != snapshot.mission_options:
            self.mission_input.blockSignals(True)
            self.mission_input.clear()
            self.mission_input.addItems(snapshot.mission_options)
            self.mission_input.blockSignals(False)

        if self.mission_input.currentText().strip() != snapshot.mission_text:
            self.mission_input.blockSignals(True)
            self.mission_input.setCurrentText(snapshot.mission_text)
            self.mission_input.blockSignals(False)

    def _sync_teaching_inputs(self, snapshot: SupervisorSnapshot) -> None:
        current_profile_items = [
            self.teaching_profile_input.itemData(index)
            for index in range(self.teaching_profile_input.count())
        ]
        snapshot_profile_items = [name for name, _title in snapshot.teaching_profile_options]
        if current_profile_items != snapshot_profile_items:
            self.teaching_profile_input.blockSignals(True)
            self.teaching_profile_input.clear()
            for name, title in snapshot.teaching_profile_options:
                self.teaching_profile_input.addItem(title, name)
            self.teaching_profile_input.blockSignals(False)

        checkbox_state = self.teaching_mode_checkbox.isChecked()
        if checkbox_state != snapshot.teaching_mode_enabled:
            self.teaching_mode_checkbox.blockSignals(True)
            self.teaching_mode_checkbox.setChecked(snapshot.teaching_mode_enabled)
            self.teaching_mode_checkbox.blockSignals(False)

        selected_profile = self.teaching_profile_input.currentData()
        if selected_profile != snapshot.teaching_profile_name:
            index = self.teaching_profile_input.findData(snapshot.teaching_profile_name)
            if index >= 0:
                self.teaching_profile_input.blockSignals(True)
                self.teaching_profile_input.setCurrentIndex(index)
                self.teaching_profile_input.blockSignals(False)

        notes_value = self.teaching_notes_input.toPlainText().strip()
        if not self.teaching_notes_input.hasFocus() and notes_value != snapshot.teaching_notes:
            self.teaching_notes_input.blockSignals(True)
            self.teaching_notes_input.setPlainText(snapshot.teaching_notes)
            self.teaching_notes_input.blockSignals(False)

        inputs_enabled = bool(snapshot.teaching_mode_enabled)
        self.teaching_profile_input.setEnabled(inputs_enabled)
        self.teaching_notes_input.setEnabled(inputs_enabled)
        self.teaching_prompt_label.setEnabled(inputs_enabled)
        self.teaching_prompt_label.setText(snapshot.teaching_prompt_text)

    def _apply_mode(self, mode: str) -> None:
        if mode == self._current_mode:
            return
        self._current_mode = mode

        if mode == "compact":
            self.body_stack.setCurrentWidget(self.compact_page)
            self.body_stack.hide()
        elif mode == "approval":
            self.body_stack.setCurrentWidget(self.approval_page)
            self.body_stack.show()
        else:
            self.body_stack.setCurrentWidget(self.command_page)
            self.body_stack.show()

        self.setFixedSize(MODE_SIZES.get(mode, MODE_SIZES["command"]))
        self.update_position()

    @QtCore.pyqtSlot(object)
    def apply_snapshot(self, snapshot: object) -> None:
        """Render one controller snapshot into the supervisor console."""

        if not isinstance(snapshot, SupervisorSnapshot):
            return

        self._sync_mission_input(snapshot)
        self._sync_teaching_inputs(snapshot)
        self.autonomy_selector.set_level(snapshot.autonomy_level)

        self.state_dot.setText(snapshot.state_icon)
        self.state_title_label.setText(snapshot.state_text)
        self.state_subtitle_label.setText(snapshot.status_text)
        self.status_badge.setText(snapshot.status_text)
        self.status_badge.setToolTip(snapshot.status_detail)
        self.status_badge.setProperty("tone", snapshot.status_tone)
        _repolish(self.status_badge)

        self.elapsed_badge.setText(snapshot.elapsed_text)
        self.elapsed_badge.setProperty("tone", "accent" if snapshot.mode == "approval" else "info")
        _repolish(self.elapsed_badge)

        self.start_button.setEnabled(snapshot.can_start)
        self.start_button.setToolTip(snapshot.start_tooltip)
        self.start_button.setVisible(snapshot.mode == "command" and not snapshot.is_running and not snapshot.is_paused)
        self.pause_button.setVisible(snapshot.can_pause)
        self.pause_button.setEnabled(snapshot.can_pause)
        self.pause_button.setIcon(self.play_icon if snapshot.is_paused else self.pause_icon)
        self.pause_button.setToolTip(snapshot.pause_tooltip)
        self.stop_button.setVisible(snapshot.can_stop)
        self.stop_button.setEnabled(snapshot.can_stop)
        self.help_button.setVisible(snapshot.mode == "command")
        self.settings_button.setVisible(snapshot.mode == "command")

        self.intent_card.apply_state(snapshot.intent)
        self.approve_button.setEnabled(not snapshot.intent.fix_required)
        self.approve_button.setToolTip("Approve (F8)" if not snapshot.intent.fix_required else "Use Fix or No for low-confidence targets")
        self.dashboard_tab.apply_snapshot(snapshot)
        self.guidance_label.setText(
            snapshot.status_detail
            or "The console will expand automatically whenever a planner action needs approval."
        )
        if snapshot.status_tone == "danger" and snapshot.status_detail:
            self.error_label.setText(snapshot.status_detail)
            self.error_card.setVisible(True)
        else:
            self.error_card.setVisible(False)

        self._apply_mode(snapshot.mode)

    @QtCore.pyqtSlot(dict)
    def _show_planner_overlay(self, payload: dict) -> None:
        x = payload.get("absolute_x")
        y = payload.get("absolute_y")
        if x is None or y is None:
            return
        decision = payload.get("decision", {})
        self._click_overlay.show_target(
            int(x),
            int(y),
            decision.get("label", "target"),
            float(decision.get("confidence", 0.0) or 0.0),
            window_rect=payload.get("window_rect", {}),
            action_type=decision.get("action_type", "click"),
            detections=payload.get("detections", []),
            target_id=decision.get("target_id", ""),
            shortcut_hint=payload.get("shortcut_hint", "Waiting for approval"),
            thought_process=decision.get("thought_process", ""),
            sub_goal=payload.get("sub_goal", ""),
        )

    def _clear_planner_overlay(self) -> None:
        self._click_overlay.dismiss()

    @QtCore.pyqtSlot(dict)
    def _show_fix_overlay(self, payload: dict) -> None:
        self._clear_planner_overlay()
        self._correction_overlay.capture_for_window(
            payload.get("window_rect", {}),
            prompt_text=payload.get("prompt_text", "Click the correct target"),
        )

    def _clear_fix_overlay(self) -> None:
        self._correction_overlay.dismiss()

    def update_position(self) -> None:
        """Anchor the supervisor console near the target game window."""

        try:
            target_windows = gw.getWindowsWithTitle(self.target_title)
            self._cached_target_window = target_windows[0] if target_windows else None
            self._cached_active_window = gw.getActiveWindow()
        except Exception as exc:
            LOGGER.debug("UI window lookup skipped: %s", exc)
            self._cached_target_window = None
            self._cached_active_window = None

        target_window = self._cached_target_window
        active_window = self._cached_active_window
        if target_window:
            x = target_window.left + max(16, target_window.width - self.width() - 20)
            y = target_window.top + 24
            self.move(x, y)

            active_title = str(getattr(active_window, "title", "") or "")
            should_stay_on_top = active_title in {self.target_title, "OSROKBOT", "python3"}
            if should_stay_on_top != self._last_stays_on_top:
                self._last_stays_on_top = should_stay_on_top
                flags = self.windowFlags()
                if should_stay_on_top:
                    flags |= QtCore.Qt.WindowStaysOnTopHint
                else:
                    flags &= ~QtCore.Qt.WindowStaysOnTopHint
                self.setWindowFlags(flags)
                self.show()

    def start_automation(self) -> None:
        """Start the selected mission with the current autonomy level."""

        self.controller.start_automation(
            self.mission_input.currentText(),
            self._selected_autonomy_level(),
            self.teaching_mode_checkbox.isChecked(),
            str(self.teaching_profile_input.currentData() or ""),
            self.teaching_notes_input.toPlainText(),
        )

    def stop_automation(self) -> None:
        """Request shutdown of the active automation session."""

        self.controller.stop_automation()

    def toggle_pause(self) -> None:
        """Toggle the active session pause state."""

        self.controller.toggle_pause()

    def approve_planner_action(self) -> None:
        """Approve the currently pending planner action."""

        self.controller.approve_pending_action()

    def reject_planner_action(self) -> None:
        """Reject the currently pending planner action."""

        self.controller.reject_pending_action()

    def correct_planner_action(self) -> None:
        """Begin the blocking Fix workflow for the pending planner action."""

        self.controller.begin_fix_capture()

    def currentState(self, state_text: str) -> None:
        """Receive a runtime state update from the shared signal emitter."""

        self.controller.handle_runtime_state_changed(state_text)

    def on_pause_toggled(self, is_paused: bool) -> None:
        """Receive a pause-state update from the shared signal emitter."""

        self.controller.handle_pause_toggled(is_paused)

    def on_planner_decision(self, payload: dict) -> None:
        """Receive a planner-decision payload from the shared signal emitter."""

        self.controller.handle_planner_decision(payload)

    def on_yolo_weights_ready(self, success: bool, message: str) -> None:
        """Receive the completion result of the background YOLO warmup."""

        self.controller.handle_yolo_weights_ready(success, message)

    def _selected_autonomy_level(self) -> int:
        for level, button in self.autonomy_selector._buttons.items():
            if button.isChecked():
                return level
        return self.controller.snapshot().autonomy_level

    def _on_teaching_profile_changed(self) -> None:
        self.controller.set_teaching_profile_name(str(self.teaching_profile_input.currentData() or ""))

    def _on_teaching_notes_changed(self) -> None:
        self.controller.set_teaching_notes(self.teaching_notes_input.toPlainText())

    def _open_help(self) -> None:
        readme_path = PROJECT_ROOT / "README.md"
        if readme_path.is_file():
            webbrowser.open(readme_path.as_uri())

    def open_settings(self) -> None:
        """Open the runtime settings dialog and refresh the console on save."""

        dialog = SettingsDialog(self)
        if dialog.exec_():
            self.controller.refresh_after_settings()
            self.apply_snapshot(self.controller.snapshot())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Dismiss overlays, stop the controller, and accept the window close."""

        self._clear_fix_overlay()
        self._clear_planner_overlay()
        if self._tray:
            self._tray.hide()
        self.controller.shutdown()
        event.accept()


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    EmergencyStop.start_once()

    app = QtWidgets.QApplication(sys.argv)
    if HealthCheckDialog.should_show():
        health_dialog = HealthCheckDialog(window_title="Rise of Kingdoms")
        health_dialog.exec_()

    WindowHandler().activate_window("Rise of Kingdoms")
    gui = UI("Rise of Kingdoms")
    sys.exit(app.exec_())
