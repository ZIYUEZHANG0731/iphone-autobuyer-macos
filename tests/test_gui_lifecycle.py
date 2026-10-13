"""Exercise shutdown with populated accessible widgets and real Qt threads."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize('exit_method', ['close', 'quit'])
def test_accessible_window_and_worker_exit_cleanly(tmp_path, exit_method):
    script=textwrap.dedent('''
        import sys
        import threading
        from pathlib import Path
        from PySide6.QtCore import QThread, QTimer
        from PySide6.QtGui import QAccessible
        from PySide6.QtWidgets import QApplication, QTableWidgetItem
        from shiboken6 import delete, isValid
        from app.automation.control import Control
        from app.gui.window import MainWindow
        from app.utils.qt import prepare_plugins, single_shot

        prepare_plugins()
        qt=QApplication(['lifecycle-regression'])
        window=MainWindow(Path(sys.argv[1]))
        window.show()
        QAccessible.setActive(True)

        class WaitingWorker(QThread):
            def __init__(self, parent):
                super().__init__(parent)
                self.control=Control(False)
                self.idle=False
                self.session_open=False
                self.ready=threading.Event()
                self.cleaned=threading.Event()
            def run(self):
                self.ready.set()
                self.control.stop_event.wait(10)
                self.cleaned.set()

        worker=WaitingWorker(window)
        window.worker=worker
        worker.finished.connect(window.on_finished)
        worker.start()
        assert worker.ready.wait(3)
        qt.aboutToQuit.connect(window.shutdown)

        try:
            # The crash reports involve populated tables, list focus changes
            # and destruction. Read cell interfaces, reset rows, and revisit
            # pages before quitting instead of testing an untouched window.
            for page in (1, 2, 5, 0, 4, 1, 5):
                window.stack.setCurrentIndex(page)
                qt.processEvents()
                for table in (window.profile_table, window.available_stores,
                              window.log_table, window.stock_table, window.orders_table):
                    table.setRowCount(3)
                    for row in range(3):
                        for col in range(table.columnCount()):
                            table.setItem(row,col,QTableWidgetItem('offline fixture'))
                    interface=QAccessible.queryAccessibleInterface(table)
                    children=[interface.child(n) for n in range(interface.childCount())]
                    cells=[c for c in children if c and c.role()==QAccessible.Role.Cell]
                    assert cells and all(c.isValid() for c in cells)
                    # Qt owns these interfaces and removes cells with rows.
                    # Do not retain Python wrappers across their C++ deletion.
                    del cells, children
                    table.setRowCount(1)
                    assert interface.childCount()>0
                window.store_list.setFocus()
                qt.processEvents()

            # Exercise the same parent-owned deferred callback used by the app.
            single_shot(window,10,lambda: window.close() if sys.argv[2]=='close' else qt.quit())
            status=qt.exec()
            assert status==0
            assert worker.control.stop_event.is_set() and worker.cleaned.is_set()
            assert not worker.isRunning() and not window.timer.isActive()
        finally:
            window.shutdown()  # Safe to repeat after aboutToQuit or a test error.
            delete(window)
        assert not isValid(window)
        print('ACCESSIBLE_SHUTDOWN_OK', flush=True)
    ''')
    env={**os.environ,'QT_QPA_PLATFORM':'offscreen','AUTOBUYER_DATA_DIR':str(tmp_path),'PYTHONFAULTHANDLER':'1'}
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path),exit_method],
                          cwd=Path(__file__).resolve().parents[1],env=env,
                          text=True,encoding='utf-8',capture_output=True,timeout=25)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
    assert 'ACCESSIBLE_SHUTDOWN_OK' in result.stdout
    assert 'QThread: Destroyed' not in result.stderr


def test_actual_gui_entry_point_shuts_down_without_atexit_widgets(tmp_path):
    env={**os.environ,'QT_QPA_PLATFORM':'offscreen','AUTOBUYER_DATA_DIR':str(tmp_path)}
    result=subprocess.run([sys.executable,'-m','app.main','--smoke-test'],
                          cwd=Path(__file__).resolve().parents[1],env=env,
                          text=True,encoding='utf-8',capture_output=True,timeout=25)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
    assert 'GUI_SMOKE_OK' in result.stdout


def test_embedded_gui_entry_point_checks_the_renderer_and_exits_cleanly(tmp_path):
    env={**os.environ,'QT_QPA_PLATFORM':'offscreen','AUTOBUYER_DATA_DIR':str(tmp_path)}
    result=subprocess.run([sys.executable,'-m','app.main','--embedded-smoke-test'],
                          cwd=Path(__file__).resolve().parents[1],env=env,
                          text=True,encoding='utf-8',capture_output=True,timeout=25)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert 'EMBEDDED_SMOKE_OK' in result.stdout
    assert 'QThread: Destroyed' not in result.stderr
