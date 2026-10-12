"""Pickup-person form and management; sensitive records use the shared vault."""
from uuid import uuid4
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox, QTableWidgetItem, QVBoxLayout)
from app.models.config import ConfigError, Identity
from app.models.flow import FlowError
from app.platforms import vault_label
from app.security.vault import IdentityVault


class ProfileDialog(QDialog):
    def __init__(self,parent=None,identity=None,alias=''):
        super().__init__(parent);self.setWindowTitle('取货人');self.resize(480,350)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,18,20,18);layout.setSpacing(14)
        note=QLabel(f'Apple 取货只需证件后四位，不需要完整身份证号码。\n取货信息仅保存到{vault_label()}。');note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
        previous_type=identity.document_type if identity else '居民身份证'
        self.document_type=previous_type if '后四位' in previous_type else previous_type+'后四位'
        form=QFormLayout();form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft);form.setSpacing(10);self.fields={}
        for key,label in [('alias','显示别名'),('family_name','姓氏'),('given_name','名字'),('document_number','证件后四位'),('phone','手机号码'),('email','电子邮箱')]:
            widget=QLineEdit();widget.setAccessibleName(label)
            widget.setText(alias if key=='alias' else getattr(identity,key,'') if identity else '')
            if key=='document_number':
                if identity:widget.setText(identity.document_number[-4:].upper())
                widget.setEchoMode(QLineEdit.EchoMode.Password)
                widget.setPlaceholderText('四位数字，末位可为 X')
                # Do not use maxLength(4): pasting a full ID would silently keep
                # its first four characters rather than rejecting the input.
            self.fields[key]=widget;form.addRow(label,widget)
        layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept_checked);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def identity(self):
        values={k:v.text().strip() for k,v in self.fields.items() if k!='alias'}
        values['document_number']=values['document_number'].upper()
        return Identity(name=values['family_name']+values['given_name'],document_type=self.document_type,**values)
    def accept_checked(self):
        try:
            self.identity().validate()
            if not self.fields['alias'].text().strip():raise ConfigError('请填写不含证件号码的显示别名')
            self.accept()
        except ConfigError as exc:QMessageBox.information(self,'请检查',str(exc))



class ProfileControls:
    def build_profiles(self):
        l=self.page('取货人',f'证件、手机号和邮箱保存在{vault_label()}，本机记录只保留显示别名。')
        frame,c=self.card();self.label(c,'官网实测：姓氏 + 名字 + 联系方式 + 证件后四位','eyebrow');self.label(c,'证件只填写后四位，末位 X 也可填写。出现手机号验证时在官网填写验证码。','muted');l.addWidget(frame)
        self.profile_table=self.table(['显示别名','默认取货人','存储位置']);self.profile_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);l.addWidget(self.profile_table,1)
        row=QHBoxLayout();self.button(row,'添加取货人',self.add_profile,'primary');self.button(row,'编辑',self.edit_profile);self.button(row,'设为默认',self.default_profile);self.button(row,'删除',self.delete_profile);l.addLayout(row)
        self.label(l,'先运行官网试跑也可以不保存身份；软件会到取货人页面暂停，允许你直接在官网填写。','muted')

    def refresh_profiles(self):
        self.profiles=self.repo.profiles();self.profile_table.setRowCount(len(self.profiles))
        for row,p in enumerate(self.profiles):
            for col,value in enumerate([p['alias'],'默认' if p['id']==self.config.profile_id else '',vault_label()]):self.profile_table.setItem(row,col,QTableWidgetItem(value))

    def store_profile(self,dialog,profile_id):
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:
            identity=dialog.identity();self.redactor.register(identity);IdentityVault().put(profile_id,identity);self.repo.save_alias(profile_id,dialog.fields['alias'].text().strip())
            if not self.config.profile_id:self.config.profile_id=profile_id
            self.repo.save_config(self.config);self.refresh_profiles()
        except FlowError as exc:QMessageBox.information(self,vault_label(),str(exc))

    def add_profile(self):self.store_profile(ProfileDialog(self),str(uuid4()))

    def edit_profile(self):
        row=self.profile_table.currentRow()
        if row<0:return
        p=self.profiles[row]
        try:self.store_profile(ProfileDialog(self,IdentityVault().get(p['id']),p['alias']),p['id'])
        except FlowError as exc:QMessageBox.information(self,vault_label(),str(exc))

    def default_profile(self):
        row=self.profile_table.currentRow()
        if row>=0:self.config.profile_id=self.profiles[row]['id'];self.repo.save_config(self.config);self.refresh_profiles()

    def delete_profile(self):
        row=self.profile_table.currentRow()
        if row<0:return
        p=self.profiles[row]
        try:
            IdentityVault().delete(p['id']);self.repo.remove_alias(p['id'])
            if self.config.profile_id==p['id']:self.config.profile_id=''
            self.repo.save_config(self.config);self.refresh_profiles()
        except FlowError as exc:QMessageBox.information(self,vault_label(),str(exc))
