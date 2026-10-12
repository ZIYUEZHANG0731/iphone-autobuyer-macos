"""Browser/account UI actions; the host supplies configuration and task state."""
from copy import deepcopy
from uuid import uuid4
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget
from app.gui.account import AccountDialog
from app.models.flow import FlowError
from app.models.config import ConfigError
from app.platforms import vault_label
from app.security.vault import CredentialVault
from app.services.daily_chrome import EXTENSION_URL


class BrowserControls:
    def build_browser(self):
        page=QWidget();self.browser_layout=QVBoxLayout(page);self.browser_layout.setContentsMargins(24,18,24,16);self.browser_layout.setSpacing(12)
        self.label(self.browser_layout,'官网浏览器','title')
        self.browser_hint=self.label(self.browser_layout,'提前保存账户并完成首次验证，购买时沿用同一浏览器。验证码仍在官网页面填写。','muted')
        account_row=QHBoxLayout()
        self.account_status=QLabel();self.account_status.setObjectName('muted');account_row.addWidget(self.account_status,1)
        self.account_button=self.button(account_row,'保存 Apple 账户…',self.configure_account)
        self.remove_account_button=self.button(account_row,'删除保存账号',self.remove_account)
        self.browser_layout.addLayout(account_row)
        row=QHBoxLayout();self.button(row,'内置浏览器登录',self.login_embedded,'primary');self.button(row,'独立 Chrome 登录',self.login_chrome);row.addStretch();self.browser_layout.addLayout(row)
        row=QHBoxLayout();self.button(row,'已完成登录，检查',self.complete_browser_login)
        self.button(row,'检查登录状态',lambda:self.launch('login_check'));self.button(row,'记录登录问题',lambda:self.launch('login_diagnosis'));row.addStretch();self.browser_layout.addLayout(row)
        row=QHBoxLayout();self.button(row,'接入日常 Chrome',self.connect_daily_chrome,'primary');self.button(row,'安装 Chrome 连接扩展…',self.open_chrome_extension);row.addStretch();self.browser_layout.addLayout(row)
        self.label(self.browser_layout,'日常 Chrome：先在官网正常登录，再通过 Microsoft 官方 Playwright 扩展选择一个 Apple 中国商店页面。关闭软件会话会断开连接，Chrome 和原有登录继续保留。','muted')
        self.browser_session_note=self.label(self.browser_layout,'登录诊断会随实际运行的浏览器记录；切换浏览器需要先关闭当前会话。','muted')
        self.network_note=self.label(self.browser_layout,'登录期间自动记录本机网络变化；已有会话与验证码在同一浏览器保留。','muted')
        self.browser_holder=QWidget();self.browser_holder_layout=QVBoxLayout(self.browser_holder);self.browser_holder_layout.setContentsMargins(0,0,0,0)
        self.browser_placeholder=self.label(self.browser_holder_layout,'选择内置浏览器或独立 Chrome 登录。完成后可回控制台设置开售时间。','muted');self.browser_holder_layout.addStretch();self.browser_layout.addWidget(self.browser_holder,1)
        self.stack.addWidget(page)

    def update_account_status(self):
        saved=bool(self.config.credential_id)
        self.account_status.setText(f'已保存到{vault_label()} · '+('自动登录已开启' if self.config.auto_login else '自动登录未开启')+(' · 已预选信任此浏览器' if self.config.auto_login and self.config.trust_browser else '') if saved else '尚未保存 Apple 账户')
        self.account_button.setText('管理 Apple 账户…' if saved else '保存 Apple 账户…')
        self.remove_account_button.setEnabled(saved)

    def configure_account(self):
        if self.worker and self.worker.isRunning() and not self.worker.idle:
            self.message_label.setText('请先停止当前任务，再修改保存的 Apple 账户。');return
        try:
            vault=CredentialVault();previous=vault.get(self.config.credential_id) if self.config.credential_id else None
            dialog=AccountDialog(self,credential=previous,auto_login=self.config.auto_login,trust_browser=self.config.trust_browser)
            try:
                if dialog.exec()!=QDialog.DialogCode.Accepted:return
                credential=dialog.credential();new_id=str(uuid4());old_id=self.config.credential_id
                vault.put(new_id,credential)
                updated=deepcopy(self.config);updated.credential_id=new_id;updated.auto_login=dialog.auto_login.isChecked();updated.trust_browser=updated.auto_login and dialog.trust_browser.isChecked()
                try:self.repo.save_config(updated)
                except Exception:
                    vault.delete(new_id);raise
                self.config=updated;self.redactor.register(credential);self.update_account_status()
                self.message_label.setText(f'Apple 账户已保存到本机{vault_label()}。请提前登录并完成首次验证。')
                if old_id:vault.delete(old_id)
            finally:
                dialog.password.clear();dialog.previous=None;dialog.deleteLater()
        except (FlowError,ConfigError) as exc:QMessageBox.information(self,'Apple 账户',str(exc))

    def remove_account(self):
        if self.worker and self.worker.isRunning() and not self.worker.idle:
            self.message_label.setText('请先停止当前任务，再删除保存的 Apple 账户。');return
        if not self.config.credential_id:return
        if QMessageBox.question(self,'删除保存账号',f'删除本机{vault_label()}中的 Apple 账户和密码？浏览器里的现有登录仍然保留。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:
            self.config.auto_login=False;self.config.trust_browser=False;self.repo.save_config(self.config)
            CredentialVault().delete(self.config.credential_id)
            self.config.credential_id='';self.repo.save_config(self.config);self.update_account_status()
        except FlowError as exc:QMessageBox.information(self,'Apple 账户',str(exc));self.update_account_status()

    def login_embedded(self):
        self.login_browser('embedded')

    def login_chrome(self):
        self.login_browser('chrome')

    def login_browser(self,kind):
        if self.worker and self.worker.isRunning():
            if not self.worker.idle:
                self.message_label.setText('先停止当前任务，再切换登录浏览器。');return
            current=getattr(self.worker,'session_kind','embedded' if self.worker.embedded else 'chrome')
            if current==kind:
                self.launch('login');return
            self.pending_browser_action='login';self.browser_kind.setCurrentIndex(self.browser_kind.findData(kind));self.close_session();return
        self.browser_kind.setCurrentIndex(self.browser_kind.findData(kind));self.launch('login')

    def connect_daily_chrome(self):
        if self.worker and self.worker.isRunning():
            if not self.worker.idle:
                self.message_label.setText('请先停止当前任务，再接入日常 Chrome。');return
            if getattr(self.worker,'session_kind','')!='daily_chrome':
                self.pending_browser_action='login_check';self.browser_kind.setCurrentIndex(self.browser_kind.findData('daily_chrome'));self.close_session();return
        self.browser_kind.setCurrentIndex(self.browser_kind.findData('daily_chrome'));self.launch('login_check')

    def open_chrome_extension(self):
        from PySide6.QtCore import QProcess
        from app.automation.browser import installed_chrome
        chrome=installed_chrome()
        if not chrome:
            QMessageBox.information(self,'Chrome 连接扩展','请先安装 Google Chrome，再从官网浏览器页面安装官方扩展。');return
        # Open the official listing in the everyday profile. The user decides
        # whether to grant the extension's debugger permission in Chrome.
        started,_=QProcess.startDetached(str(chrome),[EXTENSION_URL])
        self.message_label.setText('请在日常 Chrome 审阅并安装 Microsoft 官方 Playwright Extension，再点“接入日常 Chrome”。' if started else '未能打开 Chrome，请手动在 Chrome Web Store 搜索 Playwright Extension（Microsoft）。')

    def complete_browser_login(self):
        self.launch('login_check')

    def show_active_browser(self,kind):
        if self.embedded_browser is not None:
            self.embedded_browser.setVisible(kind=='embedded')
        if kind=='embedded':
            self.browser_placeholder.hide()
        else:
            if self.browser_holder_layout.indexOf(self.browser_placeholder)<0:
                self.browser_holder_layout.addWidget(self.browser_placeholder)
            self.browser_placeholder.setText('当前使用日常 Chrome。登录与购买都在已连接的 Apple 页面进行。' if kind=='daily_chrome' else '当前使用软件独立 Chrome。登录与购买都在该 Chrome 窗口进行，可从控制台点击“打开浏览器”。')
            self.browser_placeholder.show()

    def ensure_embedded(self):
        if self.embedded_browser is None:
            from app.gui.browser import EmbeddedBrowser
            self.embedded_browser=EmbeddedBrowser(self.root,self.browser_holder)
            self.browser_placeholder.hide();self.browser_holder_layout.removeWidget(self.browser_placeholder)
            while self.browser_holder_layout.count():self.browser_holder_layout.takeAt(0)
            self.browser_holder_layout.addWidget(self.embedded_browser,1)
        self.show_active_browser('embedded')
        return {'endpoint':self.embedded_browser.endpoint,'primary_id':self.embedded_browser.primary_id,'bridge':self.embedded_browser.bridge}
