"""The same nonblocking purchase-session lock on macOS and Windows."""
from pathlib import Path
import portalocker

from app.models.flow import FlowError


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.lock = portalocker.Lock(path, 'a', timeout=0, fail_when_locked=True)

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock.acquire()
            self.path.chmod(0o600)
        except portalocker.exceptions.AlreadyLocked:
            raise FlowError('另一个购买任务正在运行，请先停止它') from None
        except (OSError, portalocker.exceptions.LockException):
            self.lock.release()
            raise FlowError('无法取得购买任务锁，请检查本机数据目录权限') from None
        return self

    def __exit__(self, *_):
        self.lock.release()
