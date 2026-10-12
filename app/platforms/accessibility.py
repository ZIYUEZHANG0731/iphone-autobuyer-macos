"""Keep Qt's Cocoa table interfaces alive while native clients inspect them."""
from pathlib import Path
import ctypes
import sys

from app.models.flow import FlowError

_library = None


def prepare_accessibility():
    global _library
    if sys.platform != 'darwin':
        return False
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QGuiApplication
    if qVersion() != '6.11.2' or QGuiApplication.platformName() != 'cocoa':
        return False
    if _library is not None:
        return True
    path = Path(__file__).parent / 'native/libautobuyer_cocoa_accessibility.dylib'
    try:
        library = ctypes.CDLL(str(path))
        install = library.ABInstallCocoaAccessibilityOwnershipFix
        install.argtypes = []
        install.restype = ctypes.c_int
        if install() != 1:
            raise OSError('Unsupported Cocoa accessibility ABI')
    except (OSError, AttributeError):
        raise FlowError('macOS 表格组件未能初始化，请重新构建或安装软件') from None
    _library = library  # Keep both replacements loaded through Qt teardown.
    return True
