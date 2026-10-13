from __future__ import annotations
from app.platforms.files import protect_path

from datetime import datetime, timezone
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QSizePolicy
from app.platforms.runtime import fit_dialog
from app.models.flow import FlowError
from app.services.payment import PaymentHandoff, PaymentStore


class PaymentDialog(QDialog):
    def __init__(self, store: PaymentStore, handoff: PaymentHandoff, parent=None):
        super().__init__(parent)
        self.store, self.handoff = store, handoff
        self.setWindowTitle("订单付款二维码")
        self.resize(460, 560)
        layout = QVBoxLayout(self)
        title = QLabel(f"订单尾号 {handoff.order_id[-4:]} · 官网付款码记录")
        title.setObjectName("subtitle")
        layout.addWidget(title)
        details = [handoff.product or "商品信息请在官网订单中核对"]
        if handoff.total_cny:
            details.append(f"RMB {handoff.total_cny}")
        if handoff.payment:
            details.append(handoff.payment + (f" · {handoff.installment_months} 期" if handoff.installment_months else ""))
        self.details = QLabel("\n".join(details))
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.pixmap = QPixmap(str(store.image_path(handoff)))
        if self.pixmap.isNull():
            raise FlowError("付款二维码图片无法读取")
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(1, 1)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image.installEventFilter(self)
        layout.addWidget(self.image, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel(f"请使用{handoff.payment_app or '官网指定的付款 App'}扫码。保存或复制后，可发给对应下单人；图片只包含官网付款码。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        row = QHBoxLayout()
        self.save_button = QPushButton("保存图片")
        self.copy_button = QPushButton("复制图片")
        self.save_button.clicked.connect(self.save_image)
        self.copy_button.clicked.connect(self.copy_image)
        row.addWidget(self.save_button)
        row.addWidget(self.copy_button)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        description = QPushButton("复制付款说明")
        description.clicked.connect(self.copy_description)
        layout.addWidget(description)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()
        fit_dialog(self)

    def refresh_image(self):
        if not self.handoff.expired():
            side = max(1, min(280, self.image.width(), self.image.height()))
            self.image.setPixmap(self.pixmap.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.FastTransformation))

    def eventFilter(self, watched, event):
        if watched is self.image and event.type() == QEvent.Type.Resize:
            self.refresh_image()
        return super().eventFilter(watched, event)

    def refresh(self):
        expired = self.handoff.expired()
        self.save_button.setEnabled(not expired)
        self.copy_button.setEnabled(not expired)
        if expired:
            self.image.clear()
            self.image.setText("付款时限已过\n请在 Apple 官网确认订单状态")
            self.status.setText("已停止显示和导出这个付款码，避免发送过期图片。")
        else:
            self.refresh_image()
            if self.handoff.payment_deadline:
                left = max(0, int((datetime.fromisoformat(self.handoff.payment_deadline) - datetime.now(timezone.utc)).total_seconds()))
                self.status.setText(f"最迟付款倒计时：{left//60:02d}:{left%60:02d}。付款状态以 Apple 官网为准。")
            elif self.handoff.payment_window_minutes:
                self.status.setText(f"官网要求 {self.handoff.payment_window_minutes} 分钟内付款；起算时间未知，请在官网确认有效性。")
            else:
                self.status.setText("官网未提供可确认的付款截止时间，请在官网确认二维码仍有效。")

    def available(self):
        if self.handoff.expired():
            self.refresh()
            QMessageBox.information(self, "付款时限已过", "请在 Apple 官网确认订单和付款状态。")
            return False
        return True

    def save_image(self):
        if not self.available():
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存付款二维码", f"Apple付款码-{self.handoff.order_id[-4:]}.png", "PNG 图片 (*.png)")
        if path and self.available():
            if not self.pixmap.save(path, "PNG"):
                QMessageBox.information(self, "未保存", "请选择可写入的图片保存位置。")
            else:
                from pathlib import Path
                protect_path(Path(path))

    def copy_image(self):
        if self.available():
            QApplication.clipboard().setImage(self.pixmap.toImage())
            self.status.setText("已复制图片，可粘贴到你要发送的聊天窗口。")

    def copy_description(self):
        h = self.handoff
        parts = [f"Apple 官网订单尾号 {h.order_id[-4:]}", h.product]
        if h.total_cny:
            parts.append(f"金额：RMB {h.total_cny}")
        if h.payment:
            parts.append(h.payment + (f" {h.installment_months} 期" if h.installment_months else ""))
        parts.append(f"请使用{h.payment_app or '官网指定 App'}扫描对应付款二维码；付款结果以 Apple 官网为准。")
        if h.payment_deadline:
            parts.append(f"本机记录的最迟付款时间：{h.payment_deadline}")
        elif h.payment_window_minutes:
            parts.append(f"官网要求 {h.payment_window_minutes} 分钟内付款，起算时间需在官网确认。")
        if h.expired():
            parts.append("付款时限已过，请勿继续使用原二维码。")
        QApplication.clipboard().setText("\n".join(p for p in parts if p))
