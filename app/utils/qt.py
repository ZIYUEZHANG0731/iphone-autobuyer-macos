"""Keep Qt plugins discoverable when a workspace hides ignored build files."""
from pathlib import Path
import os
import stat
import sys


def single_shot(parent, milliseconds, callback):
    """Keep deferred callbacks owned by their Qt parent."""
    from PySide6.QtCore import QTimer
    timer=QTimer(parent)
    timer.setSingleShot(True)
    timer.timeout.connect(callback)
    timer.timeout.connect(timer.deleteLater)
    timer.start(milliseconds)
    return timer


def prepare_plugins():
    from PySide6.QtCore import QCoreApplication, Qt
    if QCoreApplication.instance() is None:
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    if sys.platform!='darwin':
        return
    from PySide6.QtCore import QLibraryInfo
    root=Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    # macOS UF_HIDDEN is a Finder flag, not file permissions or code signing.
    # Qt excludes such files from plugin discovery, even when they are readable.
    for path in [root,*root.rglob('*')]:
        try:
            flags=path.stat().st_flags
            if flags & stat.UF_HIDDEN:
                os.chflags(path,flags & ~stat.UF_HIDDEN)
        except (OSError,AttributeError):
            pass
