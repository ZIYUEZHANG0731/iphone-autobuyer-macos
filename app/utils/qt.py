"""Keep Qt plugins discoverable when a workspace hides ignored build files."""
from pathlib import Path
import os
import stat
import sys

def prepare_plugins():
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
