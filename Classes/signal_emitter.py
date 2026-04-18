from PyQt5.QtCore import QObject, pyqtSignal


class SignalEmitter(QObject):
    pause_toggled = pyqtSignal(bool)
    state_changed = pyqtSignal(str)
    planner_decision = pyqtSignal(dict)
    yolo_weights_ready = pyqtSignal(bool, str)
    run_finished = pyqtSignal(dict)
