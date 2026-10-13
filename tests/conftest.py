"""No regression test may silently use the user's production data directory."""
import os
import pytest


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTOBUYER_DATA_DIR', str(tmp_path / '应用数据 fixture'))
    monkeypatch.setenv('PYTHONUTF8', '1')
    monkeypatch.setenv('PYTHONIOENCODING', 'utf-8')


# GUI fixtures import Qt at module scope but create QApplication later.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
