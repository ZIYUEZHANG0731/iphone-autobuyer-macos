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


def test_gui_old_berlin_schedule_keeps_exact_instant_in_beijing_only(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        window.config.display_timezone='Europe/Berlin'
        window.config.starts_at='2026-10-16T14:00:00+02:00'
        window.populate();window.tick()
        collected=window.collect()
        assert datetime.fromisoformat(collected.starts_at)==datetime.fromisoformat(window.config.starts_at)
        assert collected.display_timezone=='Asia/Shanghai'
        assert window.timezone.text()=='北京时间'
        assert window.schedule.dateTime().toPython().hour==20
        assert '北京时间 2026-10-16 20:00:00' in window.schedule_note.text()
        assert '柏林' not in window.schedule_note.text()
        window.repo.save_config(collected)
        window.config=window.repo.load_config();window.populate();window.tick()
        assert datetime.fromisoformat(window.collect().starts_at)==datetime(2026,10,16,12,tzinfo=timezone.utc)
        assert window.clock_caption.text()=='北京时间'
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


def test_default_checkout_privacy_does_not_open_a_second_confirmation_window(qt,tmp_path):
    window=MainWindow(tmp_path);control=Control(False)
    window.worker=SimpleNamespace(isRunning=lambda:True,idle=False,control=control,session_open=True)
    window.last_state='PAUSED'
    data={'key':'offline-current-page','scope':'secure.www.apple.com.cn','details':'离线协议正文',
          'choices':['我同意Apple 的隐私政策','我了解，有限的个人信息可能会在中国境外存储和处理。']}
    try:
        assert window.privacy_policy.isChecked() and window.privacy_transfer.isChecked()
        window.show_consent_help(data)
        assert window.consent_dialog is None and control.consent_approval is None
        assert window.consent_entry.isHidden() and window.console_consent_entry.isHidden()
        assert not window.consent_entry.isEnabled()
    finally:window.worker=None;window.close();window.deleteLater();qt.processEvents()


def test_unrecognized_privacy_controls_are_diagnostics_without_a_modal(qt,tmp_path,monkeypatch):
    window=MainWindow(tmp_path);control=Control(False)
    window.worker=SimpleNamespace(isRunning=lambda:True,idle=False,control=control,session_open=True)
    monkeypatch.setattr(window.tray,'showMessage',lambda *_:None)
    data={'reason':'官网隐私选项结构无法确认，页面已保留；请查看官网提示'}
    try:
        window.on_data('consent_required',data)
        window.on_event({'state':'PAUSED','message':data['reason']})
        assert window.consent_dialog is None
        assert window.consent_entry.isHidden() and window.console_consent_entry.isHidden()
        assert not window.consent_entry.isEnabled() and control.consent_approval is None
        window.on_event({'state':'CUSTOMER_INFO_FILLED','message':'继续到取货信息'})
        assert window.pending_consent is None
    finally:window.worker=None;window.close();window.deleteLater();qt.processEvents()


def test_new_task_and_cancel_remove_stale_consent_entries(qt,tmp_path):
    window=MainWindow(tmp_path);control=Control(False)
    worker=SimpleNamespace(isRunning=lambda:True,idle=False,control=control,session_open=True,
                           stop_task=control.cancel_task)
    window.worker=worker;window.last_state='PAUSED'
    data={'key':'offline-cancel-page','scope':'secure.www.apple.com.cn','details':'当前离线协议正文',
          'choices':['我同意Apple 的隐私政策','我了解，有限的个人信息可能会在中国境外存储和处理。']}
    try:
        window.show_consent_help(data);assert window.consent_entry.isHidden()
        window.stop()
        assert window.pending_consent is None and not window.consent_entry.isEnabled()
        assert window.consent_dialog is None and control.consent_approval is None
        control.reset_task();window.show_consent_help(data)
        window.on_event({'state':'PREPARING','message':'新任务'})
        assert window.pending_consent is None and not window.consent_entry.isEnabled()
    finally:window.worker=None;window.close();window.deleteLater();qt.processEvents()


def test_privacy_can_be_selected_before_checkout_and_survives_reopening_the_app(qt,tmp_path):
    from app.services.consent import NOTICE_VERSION
    window=MainWindow(tmp_path)
    try:
        assert window.privacy_policy.isChecked() and window.privacy_transfer.isChecked()
        window.privacy_policy.setChecked(True);window.privacy_transfer.setChecked(True);window.save()
        saved=window.repo.load_config()
        assert saved.privacy_policy_accepted and saved.privacy_transfer_acknowledged
        assert saved.privacy_notice_version==NOTICE_VERSION
        assert not saved.auto_submit and not saved.transmit_identity
    finally:window.close();window.deleteLater();qt.processEvents()
    reopened=MainWindow(tmp_path)
    try:
        assert reopened.privacy_policy.isChecked() and reopened.privacy_transfer.isChecked()
        assert not reopened.consent_entry.isEnabled()
        assert reopened.consent_entry.isHidden()
        assert '自动同意已启用' in reopened.consent_status.text()
        reopened.privacy_transfer.setChecked(False);reopened.save()
        assert not reopened.repo.load_config().privacy_transfer_acknowledged
    finally:reopened.close();reopened.deleteLater();qt.processEvents()


def test_imported_config_does_not_grant_privacy_or_transaction_authority(qt,tmp_path,monkeypatch):
    import json
    from app.services.consent import NOTICE_VERSION
    window=MainWindow(tmp_path)
    path=tmp_path/'import.json'
    imported=deepcopy(window.config)
    imported.auto_submit=True;imported.transmit_identity=True
    imported.privacy_policy_accepted=True;imported.privacy_transfer_acknowledged=True
    imported.privacy_notice_version=NOTICE_VERSION
    path.write_text(json.dumps(imported.to_dict()),encoding='utf-8')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *_:(str(path),'JSON'))
    try:
        window.import_config();saved=window.repo.load_config()
        assert not saved.auto_submit and not saved.transmit_identity
        assert not saved.privacy_policy_accepted and not saved.privacy_transfer_acknowledged
        assert saved.privacy_notice_version==''
        assert not window.privacy_policy.isChecked() and not window.privacy_transfer.isChecked()
    finally:window.close();window.deleteLater();qt.processEvents()


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
        assert config.product.url=='https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro/mjya4ch/a'
        choice.replace_offers([o for o in choice.offers if o['name']=='iPhone Duo'])
        assert choice.model.currentText()=='iPhone 18 Pro Max'
        assert choice.selected_options()==config.product.options
    finally:window.close();window.deleteLater();qt.processEvents()


def test_official_duo_time_can_be_selected_without_typing_or_changing_the_instant(qt,tmp_path):
    window=MainWindow(tmp_path)
    try:
        window.product_choices.model.setCurrentIndex(window.product_choices.model.findText('iPhone Duo'))
        window.use_official_time()
        assert window.schedule_enabled.isChecked()
        assert datetime.fromisoformat(window.collect().starts_at)==datetime(2026,10,16,12,tzinfo=timezone.utc)
        assert window.schedule.dateTime().toPython().hour==20
        assert '柏林' not in window.schedule_note.text()
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


def test_directory_can_select_stores_before_duo_has_a_link_and_preserves_each_city(qt,tmp_path):
    from PySide6.QtCore import Qt
    window=MainWindow(tmp_path)
    try:
        window.config.product.name='iPhone Duo';window.config.product.url='';window.url.clear()
        window.product_choices.offer['url']='';window.product_choices.offer['variants']=[]
        window.store_list.clear()
        assert window.available_stores.rowCount()==49 and not window.store_results
        for name in ('Apple 南京东路','Apple 三里屯'):
            window.search.setText(name)
            rows=[row for row in range(window.available_stores.rowCount())
                  if not window.available_stores.isRowHidden(row)]
            assert len(rows)==1
            window.available_stores.selectRow(rows[0]);window.add_selected_store()
            window.add_selected_store()  # No duplicate on repeated/double click.
        assert window.store_list.count()==2
        window.store_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        window.save()
        saved=window.repo.load_config()
        assert saved.product.url=='' and saved.product.discover_from_store
        assert [(s.name,s.city,s.district,s.enabled) for s in saved.stores]==[
            ('Apple 南京东路','上海','黄浦区',True),('Apple 三里屯','北京','朝阳区',False)]
        # Checking another city in the directory is a filter, not a rewrite.
        window.search.clear();window.store_city.setCurrentIndex(window.store_city.findData('杭州'))
        assert window.collect().stores==saved.stores
        assert all(window.available_stores.item(r,1).text()=='杭州'
                   for r in range(49) if not window.available_stores.isRowHidden(r))
    finally:window.close();window.deleteLater();qt.processEvents()


def test_upgrade_keeps_existing_chrome_login_and_guest_strategy_is_click_selectable(qt,tmp_path):
    from app.models.config import Settings
    previous=Settings().to_dict();previous.pop('browser_kind')
    assert Settings.from_dict(previous).browser_kind=='chrome'
    window=MainWindow(tmp_path)
    try:
        assert not window.checkout_strategy.isEditable() and not window.browser_kind.isEditable()
        window.checkout_strategy.setCurrentIndex(window.checkout_strategy.findData('account_only'))
        window.save()
        assert window.repo.load_config().checkout_strategy=='account_only'
        assert window.repo.load_config().browser_kind=='embedded'
    finally:window.close();window.deleteLater();qt.processEvents()


def test_window_close_keeps_an_open_browser_worker_alive(qt,tmp_path,monkeypatch):
    from PySide6.QtGui import QCloseEvent
    window=MainWindow(tmp_path);control=Control(False)
    window.worker=SimpleNamespace(isRunning=lambda:True,idle=True,control=control,session_open=True)
    monkeypatch.setattr(window.tray,'showMessage',lambda *_:None)
    try:
        event=QCloseEvent();window.closeEvent(event)
        assert not event.isAccepted() and not control.stop_event.is_set()
    finally:window.worker=None;window.close();window.deleteLater();qt.processEvents()
