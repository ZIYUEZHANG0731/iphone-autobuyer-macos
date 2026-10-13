"""Click-only payment preferences, preserving saved method order and terms."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QListWidget, QListWidgetItem, QAbstractItemView
from app.services.payment_methods import PAYMENT_METHODS, installment_payment


# Preferences, not a promise of availability. The adapter still reads the
# actual checkout radios and verifies the selected term before proceeding.
class PaymentChoices:
    def __init__(self):
        self.primary = QComboBox()
        self.primary.setAccessibleName('首选付款方式')
        self.backups = QListWidget()
        self.backups.setAccessibleName('备用付款方式：勾选后按列表顺序尝试，可拖动排序')
        self.backups.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.backups.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.backups.setMinimumHeight(130)
        self.backups.setMaximumHeight(160)
        self.terms = QComboBox()
        self.terms.setAccessibleName('分期期数')
        self.terms.addItem('不选分期', 0)
        for months in (1, 3, 6, 12, 24):
            self.terms.addItem(f'{months} 期', months)
        self.months=0
        self.terms.currentIndexChanged.connect(self.term_changed)
        self.primary.currentIndexChanged.connect(self.sync_primary)

    def populate(self, methods, months):
        self.primary.blockSignals(True)
        self.primary.clear()
        for method in dict.fromkeys((*PAYMENT_METHODS, *methods)):
            self.primary.addItem(method, method)
        self.primary.setCurrentIndex(self.primary.findData(methods[0]))
        self.backups.clear()
        for method in dict.fromkeys((*methods, *PAYMENT_METHODS)):
            item = QListWidgetItem(method)
            item.setData(Qt.ItemDataRole.UserRole, method)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if method in methods[1:] else Qt.CheckState.Unchecked)
            self.backups.addItem(item)
        self.primary.blockSignals(False)
        if self.terms.findData(months) < 0:
            self.terms.addItem(f'{months} 期（已有配置，需官网验证）', months)
        self.months=months
        self.sync_primary()

    def term_changed(self):
        if self.terms.isEnabled():self.months=self.terms.currentData()

    def sync_primary(self):
        primary = self.primary.currentData()
        self.terms.blockSignals(True)
        self.terms.setEnabled(installment_payment(primary))
        self.terms.setCurrentIndex(self.terms.findData(self.months) if self.terms.isEnabled() else 0)
        self.terms.blockSignals(False)
        for row in range(self.backups.count()):
            item = self.backups.item(row)
            flags = item.flags() | Qt.ItemFlag.ItemIsEnabled
            item.setFlags(flags & ~Qt.ItemFlag.ItemIsEnabled if item.data(Qt.ItemDataRole.UserRole) == primary else flags)

    def selected(self):
        primary = self.primary.currentData()
        result = [primary] if primary else []
        for row in range(self.backups.count()):
            item = self.backups.item(row)
            method = item.data(Qt.ItemDataRole.UserRole)
            if item.checkState() == Qt.CheckState.Checked and method not in result:
                result.append(method)
        return result
