from PyQt5.QtCore import QObject, pyqtSignal

class SignalEmitter(QObject):
    pause_toggled = pyqtSignal(bool)
    state_changed = pyqtSignal(str)
