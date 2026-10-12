from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QScrollArea, QWidget
from app.services.readiness import PreparationItem


class PreparationDialog(QDialog):
    def __init__(self, items: list[PreparationItem], navigate, parent=None):
        super().__init__(parent)
        self.setWindowTitle('开售前准备检查')
        self.resize(680, 590)
        layout = QVBoxLayout(self)
        title = QLabel('开售前准备检查'); title.setObjectName('title'); layout.addWidget(title)
        note = QLabel('这份清单检查本机准备情况；商品、库存、登录和付款仍以官网为准。')
        note.setWordWrap(True); note.setObjectName('muted'); layout.addWidget(note)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); rows = QVBoxLayout(content)
        labels = {'ready': '已配置', 'missing': '需处理', 'unknown': '待检查', 'manual': '需人工确认'}
        for item in items:
            card = QFrame(); card.setObjectName('card'); row = QHBoxLayout(card)
            text = QLabel(f'{labels[item.status]} · {item.title}\n{item.detail}')
            text.setTextFormat(Qt.TextFormat.PlainText)
            text.setWordWrap(True)
            text.setAccessibleName(f'{labels[item.status]} · {item.title}：{item.detail}')
            row.addWidget(text, 1)
            button = QPushButton(item.action)
            button.clicked.connect(lambda _, value=item: self.choose(value, navigate))
            row.addWidget(button); rows.addWidget(card)
        rows.addStretch(); scroll.setWidget(content); layout.addWidget(scroll, 1)
        close = QPushButton('返回控制台'); close.clicked.connect(self.close); layout.addWidget(close)

    def choose(self, item, navigate):
        self.close()
        navigate(item)
