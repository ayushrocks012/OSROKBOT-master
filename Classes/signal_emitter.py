from PyQt5.QtCore import QObject, pyqtSignal


class SignalEmitter(QObject):
    """Qt signal bridge shared between the runtime and supervisor console."""

    pause_toggled = pyqtSignal(bool)
    state_changed = pyqtSignal(str)
    planner_decision = pyqtSignal(dict)
    planner_trace = pyqtSignal(dict)
    yolo_weights_ready = pyqtSignal(bool, str)
    run_finished = pyqtSignal(dict)
