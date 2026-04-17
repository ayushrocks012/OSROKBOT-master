import json
import os
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pygetwindow as gw
from action_sets import ActionSets
from click_overlay import ClickOverlay
from config_manager import ConfigManager
from context import Context
from emergency_stop import EmergencyStop
from health_check import HealthCheckDialog
from input_controller import InputController
from logging_config import get_logger
from model_manager import ModelManager, yolo_download_required
from object_detector import create_detector
from OS_ROKBOT import OSROKBOT
from PyQt5 import QtCore, QtGui, QtWidgets
from session_logger import SessionLogger
from window_handler import WindowHandler

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGGER = get_logger(__name__)
EmergencyStop.start_once()


def asset_path(*parts):
    return str(PROJECT_ROOT.joinpath(*parts))


# ── Mission presets ──────────────────────────────────────────────────────────
MISSION_PRESETS = [
    "Farm the nearest useful resource safely.",
    "Gather wood (level 4+ preferred).",
    "Complete daily objectives.",
    "Navigate visible prompts and wait when uncertain.",
    "Answer Lyceum questions.",
    "Farm the nearest level 4 wood node without spending action points.",
    "Continue the current gathering flow safely. Stop if a CAPTCHA appears.",
]

MAX_MISSION_HISTORY = 10


# ── Error guidance mapping ───────────────────────────────────────────────────
ERROR_GUIDANCE = {
    "Interception unavailable": (
        "Install the Oblita Interception driver as Administrator and reboot.\n"
        "See README § Setup for detailed instructions."
    ),
    "Game not foreground": (
        "Click the Rise of Kingdoms window or press Alt+Tab to bring it forward.\n"
        "OSROKBOT needs the game window in the foreground to send input."
    ),
    "No planner action pending": (
        "The AI is still thinking, or an error occurred.\n"
        "Check the debug panel for details."
    ),
    "Captcha detected": (
        "A CAPTCHA was detected. Solve it manually in the game window,\n"
        "then resume automation."
    ),
}


# ── Settings dialog ──────────────────────────────────────────────────────────
class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = ConfigManager()
        self.setWindowTitle("OSROKBOT Settings")
        self.setMinimumWidth(520)
        self.setStyleSheet("""
            QDialog, QWidget { background-color: #2a2a2a; color: #f5f5f5; }
            QLabel { color: #f5f5f5; }
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
                padding: 5px;
            }
        """)

        self.openai_key_input = QtWidgets.QLineEdit(self.config.get("OPENAI_KEY", "") or "")
        self.openai_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.email_input = QtWidgets.QLineEdit(self.config.get("EMAIL", "") or self.config.get("EMAIL_TO", "") or "")
        self.tesseract_input = QtWidgets.QLineEdit(self.config.get("TESSERACT_PATH", "") or "")
        self.yolo_weights_input = QtWidgets.QLineEdit(self.config.get("ROK_YOLO_WEIGHTS", "") or "")
        self.yolo_url_input = QtWidgets.QLineEdit(self.config.get("ROK_YOLO_WEIGHTS_URL", "") or "")
        self.model_input = QtWidgets.QLineEdit(self.config.get("OPENAI_VISION_MODEL", "gpt-5.4-mini") or "gpt-5.4-mini")
        self.planner_goal_input = QtWidgets.QLineEdit(
            self.config.get("PLANNER_GOAL", "Safely continue the selected Rise of Kingdoms task.") or ""
        )
        self.status_label = QtWidgets.QLabel("")

        form = QtWidgets.QFormLayout()
        form.addRow("OpenAI API Key", self.openai_key_input)
        form.addRow("Notification Email", self.email_input)
        form.addRow("Tesseract Path", self._path_row(self.tesseract_input, self.browse_tesseract))
        form.addRow("YOLO Weights", self._path_row(self.yolo_weights_input, self.browse_yolo_weights))
        form.addRow("YOLO Weights URL", self.yolo_url_input)
        form.addRow("OpenAI Model", self.model_input)
        form.addRow("Planner Goal", self.planner_goal_input)

        save_button = QtWidgets.QPushButton("Save")
        save_button.clicked.connect(self.save_settings)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.reject)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(close_button)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)
        self.setLayout(layout)

    def _path_row(self, line_edit, browse_handler):
        row = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        browse_button = QtWidgets.QPushButton("Browse...")
        browse_button.clicked.connect(browse_handler)
        layout.addWidget(line_edit)
        layout.addWidget(browse_button)
        return row

    def browse_tesseract(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select tesseract.exe",
            "",
            "Executable Files (*.exe);;All Files (*)",
        )
        if path:
            self.tesseract_input.setText(path)

    def browse_yolo_weights(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select YOLO weights",
            "",
            "YOLO Weights (*.pt);;All Files (*)",
        )
        if path:
            self.yolo_weights_input.setText(path)

    def save_settings(self):
        self.config.set_many(
            {
                "OPENAI_KEY": self.openai_key_input.text(),
                "EMAIL": self.email_input.text(),
                "TESSERACT_PATH": self.tesseract_input.text(),
                "ROK_YOLO_WEIGHTS": self.yolo_weights_input.text(),
                "ROK_YOLO_WEIGHTS_URL": self.yolo_url_input.text(),
                "OPENAI_VISION_MODEL": self.model_input.text(),
                "PLANNER_GOAL": self.planner_goal_input.text(),
            }
        )
        weights_path = ModelManager(self.config).ensure_yolo_weights()
        if weights_path:
            self.yolo_weights_input.setText(str(weights_path))
            self.status_label.setText(f"Saved. YOLO weights ready: {weights_path.name}")
        else:
            self.status_label.setText("Saved. YOLO weights are optional and not configured.")


# ── Session dashboard dialog ─────────────────────────────────────────────────
class SessionDashboard(QtWidgets.QDialog):
    """Displays live session stats and a scrollable action timeline."""

    def __init__(self, session_logger=None, parent=None):
        super().__init__(parent)
        self.logger = session_logger
        self.setWindowTitle("OSROKBOT — Session Dashboard")
        self.setMinimumSize(480, 400)
        self.setStyleSheet("""
            QDialog, QWidget { background-color: #2a2a2a; color: #f5f5f5; }
            QLabel { color: #f5f5f5; font-size: 12px; }
            QTextEdit {
                background-color: #1e1e1e; color: #ddd;
                border: 1px solid #4a90e2; border-radius: 4px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 11px;
            }
        """)

        layout = QtWidgets.QVBoxLayout(self)

        # Header.
        header = QtWidgets.QLabel("📊 Session Dashboard")
        header.setStyleSheet("font-size: 15px; font-weight: bold; color: #4a90e2;")
        layout.addWidget(header)

        # Stats grid.
        self.stats_label = QtWidgets.QLabel("")
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)

        # Timeline.
        timeline_header = QtWidgets.QLabel("Action Timeline")
        timeline_header.setStyleSheet("font-weight: bold; color: #aaa; font-size: 12px;")
        layout.addWidget(timeline_header)

        self.timeline_view = QtWidgets.QTextEdit()
        self.timeline_view.setReadOnly(True)
        layout.addWidget(self.timeline_view)

        # Refresh button.
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        refresh_btn = QtWidgets.QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(refresh_btn)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # Auto-refresh timer.
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(3000)

        self.refresh()

    def refresh(self):
        if not self.logger:
            self.stats_label.setText("No active session.")
            self.timeline_view.setPlainText("")
            return

        summary = self.logger.summary()
        stats = (
            f"🎯 Mission: {summary['mission'][:80]}\n"
            f"⏱️ Duration: {summary['duration_text']}   |   "
            f"🔧 Autonomy: L{summary['autonomy_level']}\n\n"
            f"▶️ Actions: {summary['total_actions']}   |   "
            f"✅ Approved: {summary['approvals']}   |   "
            f"❌ Rejected: {summary['rejections']}   |   "
            f"🔧 Corrections: {summary['corrections']}\n"
            f"🧠 Memory hits: {summary['memory_hits']}   |   "
            f"🌐 API calls: {summary['api_calls']}   |   "
            f"⚠️ Errors: {summary['errors']}   |   "
            f"🛡️ CAPTCHAs: {summary['captchas']}"
        )
        self.stats_label.setText(stats)

        lines = []
        for event in self.logger.timeline():
            icon = {"action": "▶️", "approval": "✅", "rejection": "❌",
                    "correction": "🔧", "error": "⚠️", "captcha": "🛡️",
                    "info": "ℹ️"}.get(event["event_type"], "•")
            elapsed = f"+{event['elapsed_seconds']:.0f}s"
            detail = event.get("label") or event.get("detail") or event.get("action_type") or ""
            lines.append(f"{elapsed:>8}  {icon}  {event['event_type']:<12}  {detail}")
        self.timeline_view.setPlainText("\n".join(lines) if lines else "No events yet.")


# ── Main UI ──────────────────────────────────────────────────────────────────
class UI(QtWidgets.QWidget):
    def __init__(self, window_title, delay=0):
        super().__init__()
        os.chdir(PROJECT_ROOT)

        self.OS_ROKBOT = OSROKBOT(window_title, delay)
        self.OS_ROKBOT.signal_emitter.pause_toggled.connect(self.on_pause_toggled)
        self.OS_ROKBOT.signal_emitter.state_changed.connect(self.currentState)
        self.OS_ROKBOT.signal_emitter.planner_decision.connect(self.on_planner_decision)
        self.OS_ROKBOT.signal_emitter.yolo_weights_ready.connect(self.on_yolo_weights_ready)
        self.action_sets = ActionSets(OS_ROKBOT=self.OS_ROKBOT)
        self._background_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OSROKBOT-UI")
        self.current_context = None
        self._planner_correction_armed = False
        self._session_logger = None
        self.target_title = window_title
        self.yolo_ready = True
        self._yolo_warmup_future = None
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.update_position)
        self.timer.start(50)

        # Click target overlay.
        self._click_overlay = ClickOverlay()

        # System tray for notifications.
        self._tray = None
        self._setup_tray()

        # ── Stylesheet ──
        stylesheet = """
        QWidget {
            background-color: #2a2a2a;
        }
        QPushButton {
            background-color: #3a3a3a;
            color: #f5f5f5;
            border: 2px solid #4a90e2;
            border-radius: 8px;
            padding: 5px;
            width: 34px;
            height: 22px;
        }
        QPushButton:hover {
            background-color: #4a4a4a;
            border: 2px solid #357ab2;
        }
        QLabel {
            font-size: 16px;
            color: #f5f5f5;
            background-color: transparent;
            text-align: left !important;
        }
        QComboBox {
            border: 2px solid #4a90e2;
            border-radius: 8px;
            padding: 3px;
            font-size: Auto;
            background-color: #3a3a3a !important;
            color: white !important;
        }
        QComboBox::drop-down {
            background-color: #2a2a2a !important;
            border: 2px solid #4a90e2 !important;
            width: 10px;
            border-radius: 5px;
        }
        QComboBox::down-arrow {
            image: url(__DOWN_ARROW_ICON__);
            padding-top: 2px;
            width: 10px;
            height: 10px;
        }
        QCheckBox {
            font-size: 11px;
            color: #f5f5f5;
        }
    """
        stylesheet = stylesheet.replace(
            "__DOWN_ARROW_ICON__",
            asset_path("Media", "UI", "down_arrow.svg").replace("\\", "/"),
        )
        self.setStyleSheet(stylesheet)

        # ── Title Bar ──
        self.title_bar = QtWidgets.QWidget()
        self.title_bar.setStyleSheet("background-color: #3a3a3a; border: none;")
        title_bar_layout = QtWidgets.QHBoxLayout(self.title_bar)
        title_bar_layout.setContentsMargins(0, 0, 0, 0)
        title_bar_layout.setSpacing(0)

        self.title_label = QtWidgets.QLabel('OSROKBOT')
        self.title_label.setStyleSheet(
            "color: #f5f5f5; font-size: 14px; font-weight: bold; padding-left: 10px;"
        )
        title_bar_layout.addWidget(self.title_label)
        title_bar_layout.addStretch()

        # Help button — opens README in browser.
        self.help_button = QtWidgets.QPushButton('?')
        self.help_button.setToolTip("Open README documentation")
        self.help_button.clicked.connect(self._open_help)
        self.help_button.setStyleSheet(
            "background-color: #3a3a3a; color: #4a90e2; border: none; "
            "font-size: 16px; font-weight: bold; min-width: 5px; padding: 0px; margin: 0px;"
        )
        title_bar_layout.addWidget(self.help_button)

        # Close button.
        self.close_button = QtWidgets.QPushButton('x')
        self.close_button.clicked.connect(self.close)
        self.close_button.setStyleSheet(
            "background-color: #3a3a3a; color: #f5f5f5; border: none; "
            "font-size: 24px; min-width: 5px; min-height: 5px; padding: 0px; margin: 0px;"
        )
        self.title_bar.setFixedHeight(30)
        title_bar_layout.addWidget(self.close_button)

        # ── Status label ──
        self.status_label = QtWidgets.QLabel(' Ready!')
        self.status_label.setStyleSheet("color: #4a90e2; font-weight: bold; text-align: left !important;")
        self.status_label.setAlignment(QtCore.Qt.AlignLeft)

        # ── Button Layout ──
        button_layout = QtWidgets.QHBoxLayout()
        button_layout.setAlignment(QtCore.Qt.AlignLeft)

        self.play_button = QtWidgets.QPushButton()
        self.play_icon = QtGui.QIcon(asset_path("Media", "UI", "play_icon.svg"))
        self.play_button.setIcon(self.play_icon)
        self.play_button.setIconSize(QtCore.QSize(24, 24))
        self.play_button.setToolTip("Start (F5)")
        self.play_button.clicked.connect(self.start_automation)
        button_layout.addWidget(self.play_button)

        self.stop_button = QtWidgets.QPushButton()
        self.stop_icon = QtGui.QIcon(asset_path("Media", "UI", "stop_icon.svg"))
        self.stop_button.setIcon(self.stop_icon)
        self.stop_button.setIconSize(QtCore.QSize(24, 24))
        self.stop_button.setToolTip("Stop (F7)")
        self.stop_button.clicked.connect(self.stop_automation)
        button_layout.addWidget(self.stop_button)

        self.pause_button = QtWidgets.QPushButton()
        self.pause_icon = QtGui.QIcon(asset_path("Media", "UI", "pause_icon.svg"))
        self.unpause_icon = QtGui.QIcon(asset_path("Media", "UI", "play_icon.svg"))
        self.pause_button.setIcon(self.pause_icon)
        self.pause_button.setIconSize(QtCore.QSize(24, 24))
        self.pause_button.setToolTip("Pause/Resume (F6)")
        self.pause_button.clicked.connect(self.toggle_pause)
        button_layout.addWidget(self.pause_button)

        self.settings_button = QtWidgets.QPushButton("⚙")
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self.open_settings)
        button_layout.addWidget(self.settings_button)

        self.dashboard_button = QtWidgets.QPushButton("📊")
        self.dashboard_button.setToolTip("Session Dashboard")
        self.dashboard_button.clicked.connect(self.open_dashboard)
        button_layout.addWidget(self.dashboard_button)

        # ── Mission input (editable combo box with presets + history) ──
        self.mission_input = QtWidgets.QComboBox()
        self.mission_input.setEditable(True)
        self.mission_input.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.mission_input.lineEdit().setPlaceholderText("Type or select a mission...")
        self._populate_mission_presets()
        self.mission_input.setStyleSheet("""
            QComboBox {
                color: #fff;
                background-color: #3a3a3a;
                border: 2px solid #4a90e2;
                border-radius: 6px;
                padding: 4px;
            }
        """)

        # Captcha checkbox.
        self.check_captcha_checkbutton = QtWidgets.QCheckBox("captcha")
        self.check_captcha_checkbutton.setStyleSheet("""
            font-size: 11px;
            background-color: #3a3a3a;
            color: #fff;
            border: 2px solid #4a90e2;
            border-radius: 8px;
            padding: 2px;
        """)
        self.check_captcha_checkbutton.setChecked(True)

        # Autonomy selector.
        self.autonomy_combo_box = QtWidgets.QComboBox()
        self.autonomy_combo_box.addItems(["L1 approve", "L2 trusted", "L3 auto"])
        try:
            configured_level = int(ConfigManager().get("PLANNER_AUTONOMY_LEVEL", "1"))
            self.autonomy_combo_box.setCurrentIndex(max(0, min(2, configured_level - 1)))
        except Exception:
            self.autonomy_combo_box.setCurrentIndex(0)

        # ── Approval buttons ──
        approval_layout = QtWidgets.QHBoxLayout()
        approval_layout.setSpacing(2)
        self.approve_button = QtWidgets.QPushButton("OK")
        self.approve_button.setToolTip("Approve (F8)")
        self.reject_button = QtWidgets.QPushButton("No")
        self.reject_button.setToolTip("Reject (F9)")
        self.correct_button = QtWidgets.QPushButton("Fix")
        self.correct_button.setToolTip("Correct target (F10)")
        self.approve_button.clicked.connect(self.approve_planner_action)
        self.reject_button.clicked.connect(self.reject_planner_action)
        self.correct_button.clicked.connect(self.correct_planner_action)
        approval_layout.addWidget(self.approve_button)
        approval_layout.addWidget(self.reject_button)
        approval_layout.addWidget(self.correct_button)

        # ── Debug area ──
        debug_layout = QtWidgets.QVBoxLayout()
        debug_layout.setContentsMargins(0, 0, 0, 0)

        self.current_state_label_BG = QtWidgets.QLabel()
        self.current_state_label_BG.setFixedWidth(97)
        self.current_state_label_BG.setFixedHeight(80)
        self.current_state_label_BG.setStyleSheet(
            "text-align: center; font-size: 10px; border: 2px solid #4a90e2; "
            "border-radius: 8px; padding: 0px; background-color: #3a3a3a;"
        )
        self.current_state_label_BG.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        debug_layout.addWidget(self.current_state_label_BG)
        debug_layout.setSpacing(0)

        self.current_state_label_title = QtWidgets.QLabel('Debug')
        self.current_state_label_title.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        self.current_state_label_title.setStyleSheet(
            "text-align: center; font-size: 12px; font-weight: bold; "
            "border: 2px solid #4a90e2; border-radius: 6px; "
            "margin: 0px; padding: 0px; background-color: transparent;"
        )
        self.current_state_label = QtWidgets.QLabel("Ready!")
        self.current_state_label.setFixedWidth(97)
        self.current_state_label.setFixedHeight(70)
        self.current_state_label.setStyleSheet(
            "text-align: center; font-size: 10px; border: 0px solid #4a90e2; "
            "margin: 0px; padding: 0px; border-radius: 2px; background-color: transparent;"
        )
        self.current_state_label.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
        self.current_state_label.setContentsMargins(0, 0, 0, 0)

        text_layout = QtWidgets.QVBoxLayout(self.current_state_label_BG)
        text_layout.setSpacing(2)
        text_layout.addWidget(self.current_state_label_title)
        text_layout.addWidget(self.current_state_label)
        text_layout.setContentsMargins(0, 0, 0, 0)

        # ── Content layout ──
        content_layout = QtWidgets.QVBoxLayout()
        content_layout.setSpacing(2)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.status_label)
        button_layout.setSpacing(2)
        content_layout.addLayout(button_layout)
        content_layout.addWidget(self.mission_input)
        content_layout.addWidget(self.check_captcha_checkbutton)
        content_layout.addWidget(self.autonomy_combo_box)
        content_layout.addLayout(approval_layout)
        content_layout.addLayout(debug_layout)

        # ── Main layout ──
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(3, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(self.title_bar)
        layout.addLayout(content_layout)
        self.setLayout(layout)

        self.setWindowFlags(QtCore.Qt.FramelessWindowHint)
        self.setWindowTitle('OSROKBOT')

        self.play_button.show()
        self.stop_button.hide()
        self.pause_button.hide()
        self.setFixedWidth(145)

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.setWindowOpacity(0.75)

        # ── Keyboard shortcuts (§3.7) ──
        self._setup_shortcuts()

        self.show()
        WindowHandler().activate_window("OSROKBOT")
        self._begin_yolo_warmup()

    @staticmethod
    def yolo_warmup_required(config=None):
        return yolo_download_required(config)

    def _set_yolo_start_available(self, available, tooltip):
        self.play_button.setEnabled(available)
        self.play_button.setToolTip(tooltip)

    def _begin_yolo_warmup(self):
        if self._yolo_warmup_future and not self._yolo_warmup_future.done():
            return
        if not self.yolo_warmup_required():
            self.yolo_ready = True
            self._set_yolo_start_available(True, "Start (F5)")
            return

        self.yolo_ready = False
        self.status_label.setText(" YOLO weights downloading")
        self.status_label.setStyleSheet("color: orange; font-weight: bold;")
        self.status_label.setToolTip("A configured YOLO weights URL is being downloaded before automation starts.")
        self.current_state_label.setText("YOLO weights\ndownloading")
        self._set_yolo_start_available(False, "Waiting for YOLO weights download")
        self._yolo_warmup_future = self._background_executor.submit(ModelManager().ensure_yolo_weights)
        self._yolo_warmup_future.add_done_callback(self._emit_yolo_ready_from_future)

    def _emit_yolo_ready_from_future(self, future):
        try:
            weights_path = future.result()
        except Exception as exc:
            LOGGER.warning("YOLO weights warmup failed: %s", exc)
            self.OS_ROKBOT.signal_emitter.yolo_weights_ready.emit(False, str(exc))
            return
        if weights_path:
            self.OS_ROKBOT.signal_emitter.yolo_weights_ready.emit(True, str(weights_path))
        else:
            self.OS_ROKBOT.signal_emitter.yolo_weights_ready.emit(False, "YOLO weights download failed")

    def on_yolo_weights_ready(self, success, message):
        self._yolo_warmup_future = None
        self.yolo_ready = bool(success)
        if success:
            self.OS_ROKBOT.detector = create_detector()
            self._set_yolo_start_available(True, "Start (F5)")
            if not self.OS_ROKBOT.is_running:
                self.status_label.setText(" Ready")
                self.status_label.setStyleSheet("color: #4a90e2; font-weight: bold; text-align: left;")
                self.status_label.setToolTip("")
                self.current_state_label.setText("Ready!")
            return

        self._set_yolo_start_available(False, "YOLO weights unavailable; check Settings")
        self.status_label.setText(" YOLO weights unavailable")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.status_label.setToolTip(str(message))
        self.current_state_label.setText("YOLO weights\nfailed")

    # ── Keyboard shortcuts ───────────────────────────────────────────────────
    def _setup_shortcuts(self):
        """Register F5–F10 keyboard shortcuts."""
        QtWidgets.QShortcut(QtGui.QKeySequence("F5"), self).activated.connect(self.start_automation)
        QtWidgets.QShortcut(QtGui.QKeySequence("F6"), self).activated.connect(self.toggle_pause)
        QtWidgets.QShortcut(QtGui.QKeySequence("F7"), self).activated.connect(self.stop_automation)
        QtWidgets.QShortcut(QtGui.QKeySequence("F8"), self).activated.connect(self.approve_planner_action)
        QtWidgets.QShortcut(QtGui.QKeySequence("F9"), self).activated.connect(self.reject_planner_action)
        QtWidgets.QShortcut(QtGui.QKeySequence("F10"), self).activated.connect(self.correct_planner_action)

    # ── System tray for notifications (§3.5) ─────────────────────────────────
    def _setup_tray(self):
        """Initialise the system tray icon for toast notifications."""
        try:
            icon_path = asset_path("Media", "UI", "play_icon.svg")
            icon = QtGui.QIcon(icon_path)
            self._tray = QtWidgets.QSystemTrayIcon(icon, self)
            self._tray.setToolTip("OSROKBOT")
            self._tray.show()
        except Exception:
            self._tray = None

    def _notify(self, title, message, icon_type=QtWidgets.QSystemTrayIcon.Information):
        """Show a system tray toast notification."""
        if self._tray and self._tray.supportsMessages():
            self._tray.showMessage(title, message, icon_type, 5000)

    # ── Mission presets & history (§3.4) ──────────────────────────────────────
    def _populate_mission_presets(self):
        """Populate the mission combo box with presets and recent history."""
        config = ConfigManager()
        self.mission_input.clear()

        # Load recent history.
        history = []
        try:
            raw = config.get("MISSION_HISTORY", "")
            if raw:
                history = json.loads(raw)
                if not isinstance(history, list):
                    history = []
        except Exception:
            history = []

        # Add history first (most recent on top).
        seen = set()
        for mission in history:
            mission = str(mission).strip()
            if mission and mission not in seen:
                self.mission_input.addItem(mission)
                seen.add(mission)

        # Add presets that aren't already in history.
        for preset in MISSION_PRESETS:
            if preset not in seen:
                self.mission_input.addItem(preset)
                seen.add(preset)

        # Set current text to the configured goal or first preset.
        current_goal = config.get("PLANNER_GOAL", "") or ""
        if current_goal:
            self.mission_input.setCurrentText(current_goal)
        elif history:
            self.mission_input.setCurrentIndex(0)

    def _save_mission_to_history(self, mission):
        """Save a mission to the recent history (most recent first, max 10)."""
        config = ConfigManager()
        try:
            raw = config.get("MISSION_HISTORY", "")
            history = json.loads(raw) if raw else []
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

        # Remove duplicates and prepend.
        mission = str(mission).strip()
        history = [m for m in history if str(m).strip() != mission]
        history.insert(0, mission)
        history = history[:MAX_MISSION_HISTORY]

        config.set_many({"MISSION_HISTORY": json.dumps(history)})

    # ── Help button ──────────────────────────────────────────────────────────
    def _open_help(self):
        readme_path = PROJECT_ROOT / "README.md"
        if readme_path.is_file():
            webbrowser.open(readme_path.as_uri())

    # ── State / status handling ──────────────────────────────────────────────
    def currentState(self, state_text):
        self.current_state_label.setText(state_text)
        if "AI Recovering" in state_text:
            self.status_label.setText("🤖 AI Recovering...")
            self.status_label.setStyleSheet("color: #9be7ff; font-weight: bold;")
        elif "Learning" in state_text:
            self.status_label.setText("🧠 Learning...")
            self.status_label.setStyleSheet("color: #b8ff9b; font-weight: bold;")
        elif "Using Memory" in state_text:
            self.status_label.setText("🧠 Using Memory...")
            self.status_label.setStyleSheet("color: #b8ff9b; font-weight: bold;")
        elif "Captcha detected" in state_text:
            self.status_label.setText("Captcha detected — paused")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")
            self.status_label.setToolTip(ERROR_GUIDANCE.get("Captcha detected", ""))
            self._notify("OSROKBOT — CAPTCHA", "A CAPTCHA was detected. Solve it manually.",
                         QtWidgets.QSystemTrayIcon.Warning)
            if self._session_logger:
                self._session_logger.record_captcha()
        elif "Game not foreground" in state_text:
            self.status_label.setText("Game not foreground — paused")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")
            self.status_label.setToolTip(ERROR_GUIDANCE.get("Game not foreground", ""))
        elif "Interception unavailable" in state_text:
            self.status_label.setText("Interception unavailable")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            self.status_label.setToolTip(ERROR_GUIDANCE.get("Interception unavailable", ""))
        elif "Planner approval needed" in state_text:
            self.status_label.setText("Approve AI action")
            self.status_label.setStyleSheet("color: #9be7ff; font-weight: bold;")
            self.status_label.setToolTip("")
        elif "Planner trusted" in state_text:
            self.status_label.setText("Planner trusted auto-click")
            self.status_label.setStyleSheet("color: #b8ff9b; font-weight: bold;")
            self.status_label.setToolTip("")
        elif "Mission complete" in state_text:
            self.status_label.setText("✅ Mission complete")
            self.status_label.setStyleSheet("color: #b8ff9b; font-weight: bold;")
            self._notify("OSROKBOT — Complete", "The mission has been completed.")
        self.current_state_label.adjustSize()

    def on_planner_decision(self, payload):
        decision = payload.get("decision", {}) if isinstance(payload, dict) else {}
        label = decision.get("label", "target")
        confidence = float(decision.get("confidence", 0.0) or 0.0)
        action_type = decision.get("action_type", "click")
        x = payload.get("absolute_x")
        y = payload.get("absolute_y")
        reason = str(decision.get("reason", ""))[:80]
        self.status_label.setText(f"AI: {action_type} {label}")
        self.status_label.setStyleSheet("color: #9be7ff; font-weight: bold;")
        self.current_state_label.setText(
            f"Approve?\n{label}\nX:{x} Y:{y}\n{confidence:.2f}\n{reason}"
        )

        # Show click overlay on the game window.
        if x is not None and y is not None and action_type in {"click", "drag", "long_press"}:
            rect = payload.get("window_rect", {})
            self._click_overlay.show_target(
                int(x), int(y), label, confidence,
                window_rect=rect, action_type=action_type,
                detections=payload.get("detections", []),
                target_id=decision.get("target_id", ""),
            )

    def update_position(self):
        target_windows = gw.getWindowsWithTitle(self.target_title)
        active_window = gw.getActiveWindow()

        if target_windows and (target_windows[0].title == self.target_title or target_windows[0].title == "OSROKBOT"):
            target_window = target_windows[0]
            self.move(target_window.left + 5, target_window.top + int(target_window.height / 1.85))
            if active_window and (active_window.title == self.target_title or active_window.title == "OSROKBOT" or active_window.title == "python3"):
                self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
            else:
                self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowStaysOnTopHint)
            if not self.isVisible():
                self.show()

    def on_pause_toggled(self, is_paused):
        if is_paused:
            self.status_label.setText(' Paused')
            self.status_label.setStyleSheet("color: orange;")
            self.pause_button.setIcon(self.unpause_icon)
            self.stop_button.show()
            self.pause_button.show()
            self.play_button.hide()
        else:
            self.status_label.setText(' Running')
            self.status_label.setStyleSheet("color: green;")
            self.pause_button.setIcon(self.pause_icon)
            self.play_button.hide()
            self.pause_button.show()

    # ── Automation control ───────────────────────────────────────────────────
    def start_automation(self):
        if not self.yolo_ready:
            self.status_label.setText(" YOLO weights unavailable")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            self.current_state_label.setText("YOLO weights\nnot ready")
            return
        if self.OS_ROKBOT.is_running or not self.OS_ROKBOT.all_threads_joined:
            self.current_state_label.setText('Finishing last job\nwait 2s')
            return
        if self.OS_ROKBOT.is_paused():
            self.toggle_pause()
        WindowHandler().activate_window('Rise of Kingdoms')
        mission = self.mission_input.currentText().strip() or "Safely continue the selected Rise of Kingdoms task."
        ConfigManager().set_many({"PLANNER_GOAL": mission})
        self._save_mission_to_history(mission)

        action_group = self.action_sets.dynamic_planner()
        if action_group:
            actions_groups = [action_group]

            # Create session logger.
            autonomy_level = self.autonomy_combo_box.currentIndex() + 1
            self._session_logger = SessionLogger(mission=mission, autonomy_level=autonomy_level)

            context = Context(
                ui_instance=self,
                bot=self.OS_ROKBOT,
                signal_emitter=self.OS_ROKBOT.signal_emitter,
                window_title=self.target_title,
                session_logger=self._session_logger,
            )
            context.planner_goal = mission
            context.planner_autonomy_level = autonomy_level
            self.current_context = context
            if self.OS_ROKBOT.start(actions_groups, context):
                self.status_label.setText(' Running')
                self.status_label.setStyleSheet("color: green; font-weight: bold;")
                self._session_logger.record_info(f"Session started: {mission}")
        self.play_button.hide()
        self.stop_button.show()
        self.pause_button.show()

    def stop_automation(self):
        self.OS_ROKBOT.stop()
        self._click_overlay.dismiss()
        self.status_label.setText(' Ready')
        self.status_label.setStyleSheet("color: #4a90e2; font-weight: bold; text-align: left;")
        self.status_label.setToolTip("")
        self.play_button.show()
        self._set_yolo_start_available(self.yolo_ready, "Start (F5)" if self.yolo_ready else "YOLO weights unavailable; check Settings")
        self.stop_button.hide()
        self.pause_button.hide()
        QtCore.QTimer.singleShot(2000, lambda: self.currentState("Ready"))

        # Finalize session log.
        if self._session_logger:
            self._session_logger.record_info("Session stopped.")
            path = self._session_logger.finalize()
            if path:
                self._notify("OSROKBOT — Session Saved", f"Session log saved to {path.name}")

    def toggle_pause(self):
        self.OS_ROKBOT.toggle_pause()
        if self.OS_ROKBOT.is_paused():
            QtCore.QTimer.singleShot(2000, lambda: self.currentState("Paused"))

    def open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec_()
        self._begin_yolo_warmup()

    def open_dashboard(self):
        dialog = SessionDashboard(session_logger=self._session_logger, parent=self)
        dialog.exec_()

    # ── Planner approval ─────────────────────────────────────────────────────
    def _pending_planner_context(self):
        if not self.current_context:
            return None
        if not self.current_context.extracted.get("planner_pending"):
            self.status_label.setText("No planner action pending")
            self.status_label.setToolTip(ERROR_GUIDANCE.get("No planner action pending", ""))
            return None
        return self.current_context

    def approve_planner_action(self):
        context = self._pending_planner_context()
        if context:
            context.resolve_planner_decision(True)
            self.status_label.setText("Planner action approved")
            self.status_label.setToolTip("")
            self._click_overlay.dismiss()
            if self._session_logger:
                pending = context.extracted.get("planner_pending", {})
                label = pending.get("decision", {}).get("label", "")
                self._session_logger.record_approval(label)

    def reject_planner_action(self):
        context = self._pending_planner_context()
        if context:
            context.resolve_planner_decision(False)
            self.status_label.setText("Planner action rejected")
            self.status_label.setToolTip("")
            self._click_overlay.dismiss()
            if self._session_logger:
                pending = context.extracted.get("planner_pending", {})
                label = pending.get("decision", {}).get("label", "")
                self._session_logger.record_rejection(label)

    def correct_planner_action(self):
        context = self._pending_planner_context()
        if not context:
            return
        if not self._planner_correction_armed:
            self._planner_correction_armed = True
            self.status_label.setText("Move cursor to target")
            self._click_overlay.dismiss()
            QtCore.QTimer.singleShot(2500, self.finish_planner_correction)
            return

    def finish_planner_correction(self):
        self._planner_correction_armed = False
        context = self._pending_planner_context()
        if not context:
            return
        pending = context.extracted.get("planner_pending", {})
        rect = pending.get("window_rect", {})
        width = max(1, int(rect.get("width", 1)))
        height = max(1, int(rect.get("height", 1)))
        left = int(rect.get("left", 0))
        top = int(rect.get("top", 0))
        cursor_x, cursor_y = InputController._mouse_position()
        corrected = {
            "x": max(0.0, min(1.0, (cursor_x - left) / width)),
            "y": max(0.0, min(1.0, (cursor_y - top) / height)),
        }
        context.resolve_planner_decision(True, corrected_point=corrected)
        self.status_label.setText("Planner correction saved")
        self._click_overlay.dismiss()
        if self._session_logger:
            label = pending.get("decision", {}).get("label", "")
            self._session_logger.record_correction(label)

    def closeEvent(self, event):
        self.stop_automation()
        self._click_overlay.dismiss()
        if self._tray:
            self._tray.hide()
        self._background_executor.shutdown(wait=False, cancel_futures=True)
        event.accept()


if __name__ == "__main__":
    # Keep script-style imports and local config paths rooted at the project.
    os.chdir(PROJECT_ROOT)
    EmergencyStop.start_once()

    app = QtWidgets.QApplication(sys.argv)

    # Show health check dialog if critical components are missing (§3.3).
    if HealthCheckDialog.should_show():
        health_dialog = HealthCheckDialog(window_title="Rise of Kingdoms")
        health_dialog.exec_()

    # Activate Rise of Kingdoms window first.
    WindowHandler().activate_window('Rise of Kingdoms')
    gui = UI('Rise of Kingdoms')

    sys.exit(app.exec_())
