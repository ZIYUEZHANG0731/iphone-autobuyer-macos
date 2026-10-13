"""Keep Qt plugins discoverable when a workspace hides ignored build files."""
from app.platforms.qt import prepare_plugins


def single_shot(parent, milliseconds, callback):
    """Keep deferred callbacks owned by their Qt parent."""
    from PySide6.QtCore import QTimer
    timer=QTimer(parent)
    timer.setSingleShot(True)
    timer.timeout.connect(callback)
    timer.timeout.connect(timer.deleteLater)
    timer.start(milliseconds)
    return timer
