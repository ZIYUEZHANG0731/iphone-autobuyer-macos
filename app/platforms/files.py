"""Private files use POSIX modes or a protected Windows DACL."""
from pathlib import Path
import sys


def is_link(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def protect_path(path):
    path = Path(path)
    if is_link(path):
        raise OSError('Private path must not be a link or junction')
    if sys.platform == 'win32':
        from app.platforms.windows import protect_path as protect_windows
        protect_windows(path)
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def private_directory(path):
    path = Path(path)
    if is_link(path):
        raise OSError('Private directory must not be a link or junction')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    protect_path(path)
    return path
