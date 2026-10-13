"""A stale native cell must not crash an application table snapshot refresh."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_refresh_discards_stale_accessible_cells_and_keeps_new_rows_readable(tmp_path):
    script=textwrap.dedent('''
        import sys
        from pathlib import Path
        from PySide6.QtGui import QAccessible
        from PySide6.QtWidgets import QApplication,QTableWidgetItem
        from app.gui.window import MainWindow
        from app.utils.qt import prepare_plugins

        prepare_plugins()
        qt=QApplication(['table-refresh-regression'])
        window=MainWindow(Path(sys.argv[1]))
        QAccessible.setActive(True)
        try:
            for table in (window.profile_table,window.available_stores,window.log_table,
                          window.stock_table,window.orders_table):
                table.setRowCount(3)
                for row in range(3):
                    for col in range(table.columnCount()):
                        table.setItem(row,col,QTableWidgetItem('synthetic old cell'))
                interface=QAccessible.queryAccessibleInterface(table)
                cell=next(interface.child(n) for n in range(interface.childCount())
                          if interface.child(n).role()==QAccessible.Role.Cell)
                identifier=QAccessible.uniqueId(cell)
                del cell
                # Cocoa can release a cell while QAccessibleTable's child map
                # still holds its ID. Reproduce that ownership boundary without
                # querying system apps or requiring accessibility permission.
                QAccessible.deleteAccessibleInterface(identifier)
                assert QAccessible.accessibleInterface(identifier) is None
                table.setRowCount(3)
                table.setItem(0,0,QTableWidgetItem('synthetic edited cell'))
                edited=next(interface.child(n) for n in range(interface.childCount())
                            if interface.child(n).text(QAccessible.Text.Name)=='synthetic edited cell')
                assert edited and edited.isValid()
                del edited
                table.setRowCount(4)
                table.setItem(3,0,QTableWidgetItem('synthetic new cell'))
                fresh=next(interface.child(n) for n in range(interface.childCount())
                           if interface.child(n).text(QAccessible.Text.Name)=='synthetic new cell')
                assert fresh and fresh.isValid()
                assert fresh.text(QAccessible.Text.Name)=='synthetic new cell'
                del fresh
                table.setRowCount(1)
                assert table.rowCount()==1 and interface.childCount()>0
            print('ACCESSIBLE_REFRESH_OK',flush=True)
        finally:
            window.shutdown()
    ''')
    env={**os.environ,'QT_QPA_PLATFORM':'offscreen','AUTOBUYER_DATA_DIR':str(tmp_path),
         'PYTHONFAULTHANDLER':'1'}
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],
                          cwd=Path(__file__).resolve().parents[1],env=env,
                          text=True,capture_output=True,timeout=20)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    assert 'ACCESSIBLE_REFRESH_OK' in result.stdout
