from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.gui.window import MainWindow
from app.models.config import Product, Settings, Store
from app.services.iaone import IAOneTokenStore
from app.utils.qt import prepare_plugins


@pytest.fixture(scope='module')
def qt():
    prepare_plugins()
    yield QApplication.instance() or QApplication(['iaone-gui-test'])


def test_restock_launch_keeps_saved_schedule_but_does_not_wait_for_it(qt, tmp_path, monkeypatch):
    import app.gui.window as module
    window = MainWindow(tmp_path)
    config = Settings(product=Product(url='https://www.apple.com.cn/shop/buy-iphone/iphone-duo/mk2m4ch/a',
                       max_total='100', options={'dimensionColor': 'starwhite', 'dimensionCapacity': '256gb'}),
                      stores=[Store('Apple 南京东路', store_id='R359')], auto_submit=True,
                      browser_kind='chrome', profile_id='offline-profile', transmit_identity=True,
                      starts_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    window.config = config
    window.iaone_tokens.save('iaone_synthetic_only_not_a_real_token')
    passed = []
    class FakeWorker:
        def __init__(self, action, config, root, **kwargs):
            passed.append((action, config))
            connect = SimpleNamespace(connect=lambda _: None)
            self.event = self.data = self.completed = self.finished = connect
        def start(self):
            pass
    monkeypatch.setattr(module, 'Worker', FakeWorker)
    monkeypatch.setattr(window, 'collect', lambda: deepcopy(config))
    monkeypatch.setattr(window, 'preparation_for', lambda c, mode: [])
    try:
        window.launch('iaone')
        assert passed[0][0] == 'iaone' and not passed[0][1].starts_at
        assert window.repo.load_config().starts_at == config.starts_at
        assert config.starts_at and window.config.starts_at
        assert not window.start_iaone.isEnabled()
        assert window.banner.text() == 'IA ONE 到货后自动下单'
        window.on_data('session', {'message': 'fixture session'})
        assert window.start_iaone.isEnabled()
    finally:
        window.worker = None
        window.close()
        window.deleteLater()
        qt.processEvents()


def test_restock_does_not_silently_grant_submit_authorization(qt, tmp_path, monkeypatch):
    window = MainWindow(tmp_path)
    window.iaone_tokens.save('iaone_synthetic_only_not_a_real_token')
    config = Settings(auto_submit=False)
    monkeypatch.setattr(window, 'collect', lambda: config)
    try:
        window.launch('iaone')
        assert window.worker is None
        assert not config.auto_submit and window.stack.currentIndex() == 3
        assert '启用自动提交' in window.message_label.text()
    finally:
        window.close()
        window.deleteLater()
        qt.processEvents()


def test_unreadable_token_is_reported_without_starting_worker(qt, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from app.models.flow import FlowError
    window = MainWindow(tmp_path)
    messages = []
    monkeypatch.setattr(window, 'collect', lambda: Settings())
    def unreadable():
        raise FlowError('fixture private file unavailable')
    monkeypatch.setattr(window.iaone_tokens, 'load', unreadable)
    monkeypatch.setattr(QMessageBox, 'information', lambda _, title, message: messages.append(message))
    try:
        window.launch('iaone')
        assert window.worker is None
        assert messages == ['fixture private file unavailable']
    finally:
        window.close()
        window.deleteLater()
        qt.processEvents()


def test_close_during_restock_browser_startup_keeps_task_in_background(qt, tmp_path, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    window = MainWindow(tmp_path)
    window.worker = SimpleNamespace(action='iaone', session_open=False, isRunning=lambda: True)
    monkeypatch.setattr(window.tray, 'showMessage', lambda *args: None)
    try:
        window.show()
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted() and not window.isVisible()
        assert not window.closing
    finally:
        window.worker = None
        window.close()
        window.deleteLater()
        qt.processEvents()
