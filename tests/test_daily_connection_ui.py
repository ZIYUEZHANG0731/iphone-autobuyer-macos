from types import SimpleNamespace

import pytest

from app.gui.browser_controls import BrowserControls


@pytest.mark.parametrize('idle',[False,True])
@pytest.mark.parametrize('connected',[False,True])
def test_daily_chrome_button_reuses_the_active_or_pending_connection(idle,connected):
    actions=[]
    worker=SimpleNamespace(isRunning=lambda:True,idle=idle,
        session_kind='daily_chrome' if connected else '',session_open=connected,
        config=SimpleNamespace(browser_kind='daily_chrome'))
    host=SimpleNamespace(worker=worker,focus_browser=lambda:actions.append('focus'),
        message_label=SimpleNamespace(setText=lambda text:actions.append(text)))
    # No launch, close_session or mode selector is provided: a duplicate click
    # must retain the approved connection even while checkout is paused/busy.
    BrowserControls.connect_daily_chrome(host)
    assert ('focus' in actions)==connected
    assert len(actions)==(2 if connected else 1)
