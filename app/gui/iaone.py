"""Small connection editor; the token never enters purchase settings/logs."""
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QVBoxLayout

from app.models.flow import FlowError
from app.services.iaone import IAOneTokenStore


class IAOneControls:
    def build_iaone(self, layout):
        self.iaone_tokens = IAOneTokenStore(self.root)
        self.iaone_dialog = None
        self.iaone_label(layout)
        self.iaone_connection = self.button(layout, '连接 IA ONE…', self.configure_iaone, 'link')
        self.iaone_connection.setStyleSheet('text-align: left;')

    def iaone_label(self, layout):
        try:
            saved = bool(self.iaone_tokens.load())
            text = 'IA ONE 连接已保存 · 尚未监听' if saved else 'IA ONE：尚未连接'
        except FlowError as exc:
            text = str(exc)
        self.iaone_status = self.label(layout, text, 'muted')
        return self.iaone_status

    def configure_iaone(self):
        if self.worker and self.worker.isRunning() and not self.worker.idle:
            self.message_label.setText('请先停止当前任务，再修改 IA ONE 连接。')
            return
        if self.iaone_dialog:
            self.iaone_dialog.close()
        dialog = QDialog(self)
        self.iaone_dialog = dialog
        dialog.setWindowTitle('连接 IA ONE')
        dialog.resize(450, 210)
        layout = QVBoxLayout(dialog)
        self.label(layout, 'IA ONE → 设置 → 集成，复制令牌并保存一次。令牌加密保存在本机。')
        field = QLineEdit(dialog)
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setAccessibleName('IA ONE 令牌')
        field.setPlaceholderText('已保存连接可直接关闭；修改时输入新令牌')
        layout.addWidget(field)
        message = self.label(layout, '', 'muted')

        def save():
            try:
                self.iaone_tokens.save(field.text())
            except FlowError as exc:
                message.setText(str(exc))
                return
            field.clear()
            self.iaone_status.setText('IA ONE 连接已保存 · 尚未监听')
            dialog.accept()

        row = QHBoxLayout()
        self.button(row, '取消', dialog.reject)
        self.button(row, '保存连接', save, 'primary')
        layout.addLayout(row)
        dialog.finished.connect(lambda _: field.clear())
        dialog.show()
