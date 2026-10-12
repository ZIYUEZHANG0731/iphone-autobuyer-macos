from datetime import datetime, timedelta, timezone
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from app.gui.window import MainWindow
from app.automation.control import Control
from app.gui.payment import PaymentDialog
from app.services.payment import PaymentStore
from app.utils.qt import prepare_plugins

@pytest.fixture(scope='module')
def qt():
    prepare_plugins()
    application=QApplication.instance() or QApplication(['autobuyer-gui-safety-test'])
    yield application


def test_expired_qr_cannot_copy_or_export(qt,tmp_path,monkeypatch):
    source=tmp_path/'fixture.png'
    image=QImage(100,100,QImage.Format.Format_RGB32);image.fill(0xffffff);assert image.save(str(source),'PNG')
    store=PaymentStore(tmp_path)
    h=store.save(source.read_bytes(),order_id='W1234567890',submitted_at=datetime.now(timezone.utc)-timedelta(minutes=31),payment_window_minutes=30)
    writes=[]
    monkeypatch.setattr(QApplication,'clipboard',lambda:SimpleNamespace(setImage=lambda _:writes.append('copied')))
    monkeypatch.setattr(QMessageBox,'information',lambda *_:None)
    destination=tmp_path/'must-not-export.png'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *_:(str(destination),'PNG'))
    dialog=PaymentDialog(store,h)
    try:
        assert not dialog.copy_button.isEnabled() and not dialog.save_button.isEnabled()
        assert dialog.image.pixmap() is None or dialog.image.pixmap().isNull()
        dialog.copy_image();dialog.save_image()
        assert not writes and not destination.exists()
    finally:dialog.close();dialog.deleteLater();qt.processEvents()


def test_new_purchase_plan_preserves_old_order_lock(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        original=window.config.intent_id
        window.repo.claim_submission(original,'offline-fixture-run')
        window.repo.record_submission(original,'CONFIRMED','W1234567890')
        window.auto.setChecked(True)
        window.new_plan()
        assert window.config.intent_id!=original
        assert window.repo.submission(original)['status']=='CONFIRMED'
        assert not window.auto.isChecked()
        assert not window.repo.load_config().auto_submit
    finally:window.close();window.deleteLater();qt.processEvents()


def test_gui_timezone_switch_preserves_schedule(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        window.timezone.setCurrentIndex(window.timezone.findData('Europe/Berlin'))
        window.schedule_enabled.setChecked(True)
        window.schedule.setDateTime(window.editor_datetime(datetime(2026,10,16,14)))
        before=window.collect().starts_at
        window.timezone.setCurrentIndex(window.timezone.findData('Asia/Shanghai'))
        after=window.collect().starts_at
        assert datetime.fromisoformat(before)==datetime.fromisoformat(after)
        assert window.schedule.dateTime().toPython().hour==20
        assert '北京时间 2026-10-16 20:00:00' in window.schedule_note.text()
    finally:window.close();window.deleteLater();qt.processEvents()


def test_human_readable_product_options_preserve_exact_website_values(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        window.config.product.name='iPhone 17'
        window.config.product.url='https://www.apple.com.cn/shop/buy-iphone/iphone-17'
        window.config.product.options={'dimensionColor':'white','dimensionCapacity':'256gb'}
        window.populate()
        assert window.product_choices.controls['dimensionColor'].accessibleName()=='颜色'
        assert window.product_choices.controls['dimensionColor'].currentText()=='白色'
        assert window.product_choices.controls['dimensionCapacity'].currentText()=='256GB'
        assert window.collect().product.options=={'dimensionColor':'white','dimensionCapacity':'256gb'}
        color=window.product_choices.controls['dimensionColor'];color.setCurrentIndex(color.findData('black'))
        assert window.collect().product.options=={'dimensionColor':'black','dimensionCapacity':'256gb'}
        assert window.collect().product.max_total=='6799.00'
    finally:window.close();window.deleteLater();qt.processEvents()


def test_accessibility_navigation_selection_and_visible_page_stay_in_sync(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        # macOS accessibility can change the selected state without a mouse click.
        window.nav[5].setChecked(True)
        assert window.stack.currentIndex()==5
        assert [i for i,b in enumerate(window.nav) if b.isChecked()]==[5]
        # Preparation links and other internal navigation must also stay coherent.
        window.stack.setCurrentIndex(0)
        assert window.nav[0].isChecked() and not window.nav[5].isChecked()
        window.nav[4].click()
        assert window.stack.currentIndex()==4
    finally:window.close();window.deleteLater();qt.processEvents()


def test_rate_limit_disables_resume_and_new_browsing_until_deadline(qt,tmp_path,monkeypatch):
    import app.gui.window as window_module
    clock=[100.0];monkeypatch.setattr(window_module.time,'monotonic',lambda:clock[0])
    window=MainWindow(tmp_path);control=Control();calls=[]
    control.resume=lambda:calls.append('resume')
    window.worker=SimpleNamespace(isRunning=lambda:True,idle=False,control=control,session_open=True)
    monkeypatch.setattr(window.tray,'showMessage',lambda *_:None)
    try:
        window.on_data('http_status',{'status':541,'resource_type':'fetch','blocks_checkout':True,'wait_seconds':60})
        window.on_event({'state':'PAUSED','message':'offline limit fixture'})
        assert not window.resume_button.isEnabled() and '60' in window.resume_button.text()
        window.resume();assert not calls
        window.worker.idle=True;window.launch('login')
        assert '等待 60 秒' in window.message_label.text()
        window.worker.idle=False;clock[0]=160.1;window.tick()
        assert window.resume_button.isEnabled();window.resume();assert calls==['resume']
    finally:window.worker=None;window.close();window.deleteLater();qt.processEvents()


def test_click_only_product_payment_and_authorization_preserve_saved_preferences(qt,tmp_path):
    from PySide6.QtCore import Qt
    window=MainWindow(tmp_path)
    try:
        window.config.payments=['招商银行','微信支付','支付宝'];window.config.installment_months=24
        window.populate();assert not window.payment_choices.primary.isEditable()
        assert not window.installment.isEditable()
        window.save();saved=window.repo.load_config()
        assert saved.payments==['招商银行','微信支付','支付宝'] and saved.installment_months==24
        assert not saved.auto_submit and not saved.transmit_identity
        choice=window.product_choices;choice.model.setCurrentIndex(choice.model.findText('iPhone 18 Pro Max'))
        for key,value in [('dimensionColor','black'),('dimensionCapacity','512gb')]:
            combo=choice.controls[key];combo.setCurrentIndex(combo.findData(value))
        config=window.collect()
        assert config.product.name=='iPhone 18 Pro Max'
        assert config.product.options=={'dimensionScreensize':'6_9inch','dimensionColor':'black','dimensionCapacity':'512gb'}
        assert config.product.max_total=='12999.00'
        assert config.product.url=='https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro'
        choice.replace_offers([o for o in choice.offers if o['name']=='iPhone Duo'])
        assert choice.model.currentText()=='iPhone 18 Pro Max'
        assert choice.selected_options()==config.product.options
    finally:window.close();window.deleteLater();qt.processEvents()


def test_official_duo_time_can_be_selected_without_typing_or_changing_the_instant(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        window.product_choices.model.setCurrentIndex(window.product_choices.model.findText('iPhone Duo'))
        window.timezone.setCurrentIndex(window.timezone.findData('Europe/Berlin'))
        window.use_official_time()
        assert window.schedule_enabled.isChecked()
        assert datetime.fromisoformat(window.collect().starts_at)==datetime(2026,10,16,12,tzinfo=timezone.utc)
    finally:window.close();window.deleteLater();qt.processEvents()


def test_selected_order_opens_its_own_qr_without_replacing_latest(qt,tmp_path):
    window=MainWindow(tmp_path)
    image=QImage(100,100,QImage.Format.Format_RGB32);image.fill(0xffffff);source=tmp_path/'fixture.png';image.save(str(source),'PNG')
    try:
        for index in (0,1):
            order=f'W123456789{index}'
            window.payment_store.save(source.read_bytes(),order_id=order,product=f'离线商品{index}')
            window.repo.claim_submission(f'fixture-plan-{index}',f'fixture-run-{index}')
            window.repo.record_submission(f'fixture-plan-{index}','CONFIRMED',order)
        window.refresh_logs()
        selected=next(row for row in range(window.orders_table.rowCount()) if window.orders_table.item(row,0).text()=='7890')
        window.orders_table.selectRow(selected);window.show_selected_payment()
        assert window.payment_dialog.handoff.order_id=='W1234567890'
        assert window.payment_store.latest().order_id=='W1234567891'
        window.payment_dialog.close()
    finally:window.close();window.deleteLater();qt.processEvents()


def test_login_http_rejection_is_visible_while_waiting_for_manual_login(qt,tmp_path,monkeypatch):
    window=MainWindow(tmp_path)
    window.worker=SimpleNamespace(isRunning=lambda:True,idle=False,control=Control(),session_open=True)
    window.last_state='PAUSED'
    monkeypatch.setattr(window.tray,'showMessage',lambda *_:None)
    try:
        window.on_data('http_status',{'status':541,'resource_type':'fetch','blocks_checkout':True,
                                      'wait_seconds':60,'context':'login'})
        assert '541' in window.message_label.text() and '登录' in window.message_label.text()
        assert '会话' in window.message_label.text()
        assert not window.resume_button.isEnabled()
    finally:window.worker=None;window.close();window.deleteLater();qt.processEvents()
