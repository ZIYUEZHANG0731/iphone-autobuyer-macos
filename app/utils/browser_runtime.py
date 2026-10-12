from __future__ import annotations
import os
from pathlib import Path
import sys


def prepare_browser_runtime():
    # Playwright's frozen transport defaults to bundled .local-browsers. This app
    # deliberately installs Chromium in its normal per-user cache instead.
    if getattr(sys, 'frozen', False) and sys.platform == 'darwin':
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(Path.home() / 'Library' / 'Caches' / 'ms-playwright'))
