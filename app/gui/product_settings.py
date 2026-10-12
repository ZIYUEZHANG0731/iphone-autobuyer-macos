from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QComboBox, QSpinBox, QLabel
from app.services.catalog import OPTION_TITLES, quoted_total


class ProductChoices(QWidget):
    def __init__(self, offers):
        super().__init__()
        self.offers=deepcopy(offers);self.controls={};self.offer={}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(14)
        self.form=QFormLayout();self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft);self.form.setHorizontalSpacing(20);self.form.setVerticalSpacing(14)
        self.model=QComboBox();self.model.setAccessibleName('机型');self.form.addRow('机型',self.model)
        self.quantity=QSpinBox();self.quantity.setRange(1,10);self.quantity.setMaximumWidth(110);self.quantity.setAccessibleName('数量')
        self.form.addRow('数量',self.quantity)
        layout.addLayout(self.form)
        self.price=QLabel('官网价格：选择配置后显示');self.price.setObjectName('productTitle');layout.addWidget(self.price)
        self.note=QLabel();self.note.setWordWrap(True);self.note.setObjectName('muted');layout.addWidget(self.note)
        self.model.currentIndexChanged.connect(self.model_changed);self.quantity.valueChanged.connect(self.update_price)
        self.replace_offers(self.offers)

    def replace_offers(self, offers, *, name=None, options=None):
        selected=name or self.model.currentText()
        chosen=options if options is not None else self.selected_options()
        self.offers=deepcopy(offers)
        if selected and self.offer and not any(o['name']==selected for o in self.offers):
            previous=deepcopy(self.offer);previous['preorder_note']='官网此次未读到此机型；保留你的选择，更新后再核对。'
            self.offers.append(previous)
        self.model.blockSignals(True);self.model.clear()
        for offer in self.offers:self.model.addItem(offer['name'],offer)
        index=self.model.findText(selected)
        self.model.setCurrentIndex(index if index>=0 else 0);self.model.blockSignals(False)
        self.show_offer(self.model.currentData() or {},chosen)

    def set_product(self, product):
        offers=deepcopy(self.offers)
        if not any(o['name']==product.name for o in offers):
            offers.insert(0,{'name':product.name,'url':product.url,'groups':[]})
        self.replace_offers(offers,name=product.name,options=product.options)
        self.quantity.setValue(product.quantity)

    def model_changed(self):
        self.show_offer(self.model.currentData() or {},{})

    def show_offer(self, offer, options):
        for combo in self.controls.values():
            self.form.removeRow(combo)
        self.controls={};self.offer=deepcopy(offer)
        groups=deepcopy(offer.get('groups',[]))
        for key,value in options.items():
            if key in offer.get('locked_options',{}):continue
            if not any(g['key']==key for g in groups):
                label={'white':'白色','black':'黑色','starwhite':'星光白色','nightsky':'夜空色'}.get(value,value.upper() if value.lower().endswith(('gb','tb')) else value)
                groups.append({'key':key,'title':OPTION_TITLES.get(key,'已保存选项'),'choices':[{'value':value,'label':label}]})
        for index,group in enumerate(groups):
            combo=QComboBox();combo.setAccessibleName(group['title']);combo.addItem('请选择','')
            for choice in group['choices']:combo.addItem(choice['label'],choice['value'])
            selected=options.get(group['key'],'');position=combo.findData(selected)
            if selected and position<0:
                combo.addItem('原选择已不可用：请重新选择','');position=combo.count()-1
            combo.setCurrentIndex(max(0,position));combo.currentIndexChanged.connect(self.update_price)
            self.controls[group['key']]=combo;self.form.insertRow(index+1,group['title'],combo)
        notice=offer.get('preorder_note','')
        self.note.setText(notice or ('官网配置已读取，运行时会再次核对价格和可用选项。' if groups else '官网配置尚待同步；点击“更新官网型号与配置”。'))
        self.update_price()

    def selected_options(self):
        return {**self.offer.get('locked_options',{}),**{key:combo.currentData() for key,combo in self.controls.items() if combo.currentData()}}

    def total(self):
        if any(not combo.currentData() for combo in self.controls.values()):return '0.00'
        return quoted_total(self.offer,self.selected_options(),self.quantity.value())

    def update_price(self):
        total=self.total()
        self.price.setText(f'官网公布价格：RMB {float(total):,.2f}' if float(total)>0 else '官网价格：选择配置后显示；结账前会核对')

    def missing_choices(self):
        return [combo.accessibleName() for combo in self.controls.values() if not combo.currentData()]
