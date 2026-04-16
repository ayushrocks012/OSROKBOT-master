"""Startup health-check dialog.

Shows a checklist of required and optional components with live status
indicators. Allows inline configuration of missing required components.
"""

from pathlib import Path

from config_manager import ConfigManager
from logging_config import get_logger
from model_manager import ModelManager
from PyQt5 import QtCore, QtWidgets

LOGGER = get_logger(__name__)


_STYLE = """
QDialog, QWidget {
    background-color: #2a2a2a;
    color: #f5f5f5;
}
QLabel {
    color: #f5f5f5;
    font-size: 13px;
}
QLineEdit {
    background-color: #3a3a3a;
    color: #f5f5f5;
    border: 1px solid #4a90e2;
    border-radius: 4px;
    padding: 5px;
}
QPushButton {
    background-color: #3a3a3a;
    color: #f5f5f5;
    border: 1px solid #4a90e2;
    border-radius: 6px;
    padding: 6px 16px;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #4a4a4a;
    border: 1px solid #357ab2;
}
QPushButton#continueButton {
    background-color: #4a90e2;
    color: white;
    font-weight: bold;
}
QPushButton#continueButton:hover {
    background-color: #357ab2;
}
"""

_OK = "✅"
_WARN = "⚠️"
_FAIL = "❌"
_SPINNER = "🔄"


class HealthCheckDialog(QtWidgets.QDialog):
    """Pre-flight checklist dialog shown before OSROKBOT starts.

    Checks required and optional components, allows inline fixes, and
    always lets the user continue even with warnings.
    """

    def __init__(self, window_title="Rise of Kingdoms", parent=None):
        super().__init__(parent)
        self.config = ConfigManager()
        self.window_title = window_title
        self.setWindowTitle("OSROKBOT — Health Check")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self.setStyleSheet(_STYLE)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        # Header.
        header = QtWidgets.QLabel("🔍 Pre-flight Health Check")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #4a90e2;")
        layout.addWidget(header)

        subtitle = QtWidgets.QLabel(
            "OSROKBOT checks required and optional components before starting.\n"
            "Fix any issues below, or press Continue to start with current configuration."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 12px; color: #aaa;")
        layout.addWidget(subtitle)

        # Check rows container.
        self._checks_layout = QtWidgets.QVBoxLayout()
        self._checks_layout.setSpacing(8)
        layout.addLayout(self._checks_layout)

        # --- Check 1: OpenAI API Key ---
        self._api_row, self._api_status = self._add_check_row("OpenAI API Key", required=True)
        self._api_input = QtWidgets.QLineEdit(self.config.get("OPENAI_KEY", "") or "")
        self._api_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self._api_input.setPlaceholderText("Enter your OpenAI API key...")
        self._api_save_btn = QtWidgets.QPushButton("Save Key")
        self._api_save_btn.clicked.connect(self._save_api_key)
        api_fix = QtWidgets.QHBoxLayout()
        api_fix.addWidget(self._api_input)
        api_fix.addWidget(self._api_save_btn)
        self._api_fix_widget = QtWidgets.QWidget()
        self._api_fix_widget.setLayout(api_fix)
        self._checks_layout.addWidget(self._api_fix_widget)

        # --- Check 2: Interception Driver ---
        self._interception_row, self._interception_status = self._add_check_row(
            "Interception Driver", required=True,
            tooltip="Install the Oblita Interception driver as Administrator and reboot.\nSee README § Setup."
        )

        # --- Check 3: Game Window ---
        self._game_row, self._game_status = self._add_check_row(
            "Rise of Kingdoms Window", required=True,
            tooltip="Start Rise of Kingdoms before running OSROKBOT."
        )

        # --- Check 4: YOLO Weights (optional) ---
        self._yolo_row, self._yolo_status = self._add_check_row(
            "YOLO Detection Weights", required=False,
            tooltip="Optional. Set ROK_YOLO_WEIGHTS in Settings or download via URL."
        )
        self._yolo_download_btn = QtWidgets.QPushButton("Download Weights")
        self._yolo_download_btn.clicked.connect(self._download_yolo)
        self._checks_layout.addWidget(self._yolo_download_btn)

        # --- Check 5: Tesseract (optional) ---
        self._tesseract_row, self._tesseract_status = self._add_check_row(
            "Tesseract OCR", required=False,
            tooltip="Optional. EasyOCR is primary. You must install the Tesseract Windows executable (.exe) for this fallback to work."
        )

        layout.addStretch()

        # Buttons.
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        recheck_btn = QtWidgets.QPushButton("🔄 Re-check")
        recheck_btn.clicked.connect(self._run_checks)
        btn_layout.addWidget(recheck_btn)
        continue_btn = QtWidgets.QPushButton("Continue →")
        continue_btn.setObjectName("continueButton")
        continue_btn.clicked.connect(self.accept)
        btn_layout.addWidget(continue_btn)
        layout.addLayout(btn_layout)

        # Run checks.
        QtCore.QTimer.singleShot(100, self._run_checks)

    def _add_check_row(self, label, required=True, tooltip=None):
        """Add a status row and return the row widget + status label."""
        row = QtWidgets.QHBoxLayout()
        status_label = QtWidgets.QLabel(_SPINNER)
        status_label.setFixedWidth(28)
        status_label.setAlignment(QtCore.Qt.AlignCenter)
        text_label = QtWidgets.QLabel(label)
        tag = QtWidgets.QLabel("Required" if required else "Optional")
        tag.setStyleSheet(
            f"font-size: 10px; color: {'#ff6b6b' if required else '#888'}; "
            f"background: #333; border-radius: 3px; padding: 1px 6px;"
        )
        tag.setFixedWidth(60)
        tag.setAlignment(QtCore.Qt.AlignCenter)
        row.addWidget(status_label)
        row.addWidget(text_label, 1)
        row.addWidget(tag)
        container = QtWidgets.QWidget()
        container.setLayout(row)
        if tooltip:
            container.setToolTip(tooltip)
        self._checks_layout.addWidget(container)
        return container, status_label

    def _set_status(self, status_label, ok, optional=False):
        if ok:
            status_label.setText(_OK)
        elif optional:
            status_label.setText(_WARN)
        else:
            status_label.setText(_FAIL)

    def _run_checks(self):
        """Execute all health checks and update the status indicators."""
        self.config.load()

        # 1. OpenAI API Key.
        api_key = self.config.get("OPENAI_KEY") or self.config.get("OPENAI_API_KEY")
        has_api_key = bool(api_key and len(api_key) > 8)
        self._set_status(self._api_status, has_api_key)
        self._api_fix_widget.setVisible(not has_api_key)

        # 2. Interception.
        try:
            import interception
            interception.auto_capture_devices()
            has_interception = True
        except Exception:
            has_interception = False
        self._set_status(self._interception_status, has_interception)

        # 3. Game Window.
        try:
            import pygetwindow as gw
            windows = gw.getWindowsWithTitle(self.window_title)
            has_game = bool(windows)
        except Exception:
            has_game = False
        self._set_status(self._game_status, has_game)

        # 4. YOLO Weights.
        weights_path = self.config.get("ROK_YOLO_WEIGHTS")
        has_yolo = bool(weights_path and Path(weights_path).is_file())
        self._set_status(self._yolo_status, has_yolo, optional=True)
        self._yolo_download_btn.setVisible(not has_yolo)

        # 5. Tesseract.
        tesseract_path = self.config.get("TESSERACT_PATH")
        has_tesseract = bool(tesseract_path and Path(tesseract_path).is_file())
        self._set_status(self._tesseract_status, has_tesseract, optional=True)

    def _save_api_key(self):
        key = self._api_input.text().strip()
        if key:
            self.config.set_many({"OPENAI_KEY": key})
            self._run_checks()

    def _download_yolo(self):
        self._yolo_download_btn.setText("Downloading...")
        self._yolo_download_btn.setEnabled(False)
        QtWidgets.QApplication.processEvents()
        try:
            path = ModelManager(self.config).ensure_yolo_weights()
            if path:
                self._yolo_download_btn.setText("Downloaded ✅")
            else:
                self._yolo_download_btn.setText("Not available")
        except Exception as exc:
            self._yolo_download_btn.setText(f"Failed: {exc}")
            LOGGER.warning("YOLO download failed: %s", exc)
        self._yolo_download_btn.setEnabled(True)
        self._run_checks()

    @staticmethod
    def should_show(config=None):
        """Determine if the health check should be shown.

        Returns True when any critical component is missing.
        """
        config = config or ConfigManager()
        api_key = config.get("OPENAI_KEY") or config.get("OPENAI_API_KEY")
        if not api_key or len(api_key) < 8:
            return True
        try:
            import interception
            interception.auto_capture_devices()
        except Exception:
            return True
        return False
