from __future__ import annotations
import os
import sys
from app.platforms import browser_cache_dir


def prepare_browser_runtime():
    # Playwright's frozen transport defaults to bundled .local-browsers. This app
    # deliberately installs Chromium in its normal per-user cache instead.
    if getattr(sys, 'frozen', False):
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(browser_cache_dir()))
