"""A real embedded Chromium page with one persistent app-owned profile.

Widgets/profile live on Qt's main thread. A small queued bridge creates tabs
for the worker because Qt WebEngine does not implement CDP Target.createTarget.
No Apple credentials, cookies or page bodies are copied into application logs.
"""
import asyncio
import os
from pathlib import Path
import socket
from uuid import uuid4
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Signal, Slot, QUrl, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTabWidget
from shiboken6 import delete, isValid


class TabBridge(QObject):
    requested = Signal(object)

    async def request(self, action, target=''):
        loop = asyncio.get_running_loop()
        result = loop.create_future()
        self.requested.emit((action, target, loop, result))
        return await asyncio.wait_for(result, 5)


class EmbeddedBrowser(QWidget):
    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.endpoint = f'http://127.0.0.1:{port}'
        os.environ['QTWEBENGINE_REMOTE_DEBUGGING'] = f'127.0.0.1:{port}'
        # Avoid Qt's broad default for DevTools origins. No fingerprint changes.
        flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
        if '--remote-allow-origins=' not in flags:
            os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = flags + f' --remote-allow-origins={self.endpoint}'
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.Page, self.View = QWebEnginePage, QWebEngineView
        profile_root = root/'embedded-profile'
        profile_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        profile_root.chmod(0o700)
        for folder in ('storage', 'cache'):
            path = profile_root/folder
            path.mkdir(exist_ok=True, mode=0o700)
            path.chmod(0o700)
        self.profile = QWebEngineProfile('AppleAutoBuyer', self)
        self.profile.setPersistentStoragePath(str(profile_root/'storage'))
        self.profile.setCachePath(str(profile_root/'cache'))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        self.bridge = TabBridge(self)
        self.bridge.requested.connect(self.handle_request)
        layout = QVBoxLayout(self);layout.setContentsMargins(0, 0, 0, 0);layout.setSpacing(8)
        bar = QHBoxLayout()
        self.location = QLabel('Apple 官网');self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setAccessibleName('当前官网地址');bar.addWidget(self.location,1)
        self.back_button = QPushButton('后退');self.back_button.clicked.connect(lambda: self.current_view().back())
        bar.addWidget(self.back_button)
        self.reload_button = QPushButton('刷新');self.reload_button.clicked.connect(lambda: self.current_view().reload())
        bar.addWidget(self.reload_button);layout.addLayout(bar)
        self.tabs = QTabWidget();self.tabs.setAccessibleName('软件内置官网页面');self.tabs.currentChanged.connect(self.update_location);layout.addWidget(self.tabs,1)
        self.views = {}
        self.ready_targets = set()
        self.ready_waiters = {}
        self.primary_id = self.create_tab('购买与登录')

    def current_view(self):
        return self.tabs.currentWidget()

    def create_tab(self, title='后台检查', *, initial_blank=True):
        from PySide6.QtWebEngineCore import QWebEnginePage
        manager=self
        class QuietPage(QWebEnginePage):
            def javaScriptConsoleMessage(self, *_):
                pass
            def createWindow(self, _):
                target=manager.create_tab('官网新页面',initial_blank=False)
                manager.tabs.setCurrentWidget(manager.views[target])
                return manager.views[target].page()
        view = self.View(self.tabs)
        page = QuietPage(self.profile, view)
        view.setPage(page)
        target = page.devToolsId()
        self.views[target] = view
        self.tabs.addTab(view, title)
        page.loadFinished.connect(lambda loaded, key=target:self.tab_ready(key,loaded))
        page.titleChanged.connect(lambda name, v=view: self.update_title(v, name))
        page.urlChanged.connect(self.update_location)
        page.renderProcessTerminated.connect(lambda *_: self.location.setText('页面进程已退出，请停止任务后重新检查'))
        if initial_blank:page.setUrl(QUrl('about:blank#autobuyer-'+uuid4().hex))
        return target

    def tab_ready(self, target, loaded):
        if target in self.ready_targets:return
        if loaded:self.ready_targets.add(target)
        for loop,future,result in self.ready_waiters.pop(target,[]):
            error=None if loaded else RuntimeError('内置页面初始化未完成')
            self.send_result(loop,future,result,error)

    def wait_for_ready(self, target, loop, future, result):
        if target in self.ready_targets:self.send_result(loop,future,result,None)
        else:self.ready_waiters.setdefault(target,[]).append((loop,future,result))

    def update_title(self, view, title):
        index = self.tabs.indexOf(view)
        if index >= 0:self.tabs.setTabText(index, title[:26] or 'Apple 官网')

    def update_location(self, *_):
        view = self.current_view()
        if view is None:return
        parsed = urlsplit(view.url().toString())
        self.location.setText(f'{parsed.scheme}://{parsed.hostname or ""}{parsed.path}' if parsed.hostname else 'Apple 官网 · 等待任务')

    @Slot(object)
    def handle_request(self, request):
        action, target, loop, future = request
        if loop.is_closed() or future.done():return
        try:
            if action == 'new':
                result = self.create_tab()
                self.wait_for_ready(result,loop,future,result)
                return
            elif action == 'ready':
                if target not in self.views:raise RuntimeError('内置页面已关闭')
                self.wait_for_ready(target,loop,future,True)
                return
            elif action == 'close':
                view = self.views.pop(target, None)
                if view is not None and target != self.primary_id:
                    self.ready_targets.discard(target)
                    self.tabs.removeTab(self.tabs.indexOf(view));delete(view)
                elif view is not None:
                    self.views[target] = view
                result = True
            elif action == 'focus':
                view = self.views.get(target)
                if view is not None:self.tabs.setCurrentWidget(view)
                result = True
            else:
                raise ValueError('未知浏览器请求')
            self.send_result(loop,future,result,None)
        except Exception as error:
            self.send_result(loop,future,None,RuntimeError(type(error).__name__))

    def send_result(self, loop, future, result, error):
        if loop.is_closed() or future.done():return
        loop.call_soon_threadsafe(self.deliver,future,result,error)

    @staticmethod
    def deliver(future, result, error):
        if future.done():return
        if error:future.set_exception(error)
        else:future.set_result(result)

    def shutdown(self):
        # Pages must be destroyed before their disk-based profile so it flushes
        # normally. Never leave WebEngine objects to Python's atexit cleanup.
        for view in list(self.views.values()):
            if isValid(view):delete(view)
        self.views.clear()
        self.ready_targets.clear();self.ready_waiters.clear()
        if isValid(self.profile):delete(self.profile)
