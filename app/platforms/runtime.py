"""Desktop runtime settings that precede GUI/browser initialization."""
import sys


def prepare_stdio():
    if sys.platform == 'win32':
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8', errors='backslashreplace')


def fit_window(window):
    # Qt 6 already enables per-monitor DPI awareness. Geometry is in logical
    # pixels: allow the scrollable pages to fit 1080p screens at 150/200%.
    screen = window.screen()
    if screen:
        available = screen.availableGeometry()
        width, height = max(1, available.width()-24), max(1, available.height()-24)
        window.setMinimumSize(min(1060, width), min(720, height))
        window.resize(min(1280, width), min(825, height))
