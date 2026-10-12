"""Direct pickup-person entry; new records live only in this application session."""
from copy import deepcopy
from uuid import uuid4
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidgetItem, QVBoxLayout)
from app.models.config import ConfigError, Identity
from app.models.flow import FlowError
from app.security.vault import IdentityVault, settings_for_disk
from app.services.private_text import read_private_text


class ProfileDialog(QDialog):
    def __init__(self,parent=None,identity=None,alias=''):
        super().__init__(parent);self.setWindowTitle('取货人');self.resize(480,350)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,18,20,18);layout.setSpacing(14)
        note=QLabel('直接填写，用于 Apple 中国大陆官网取货。证件只需后四位。\n仅本次打开软件有效，不读写钥匙串。');note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
        previous_type=identity.document_type if identity else '居民身份证'
        self.document_type=previous_type if '后四位' in previous_type else previous_type+'后四位'
        form=QFormLayout();form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft);form.setSpacing(10);self.fields={}
        for key,label in [('alias','显示别名'),('family_name','姓氏'),('given_name','名字'),('document_number','证件后四位'),('phone','手机号码'),('email','电子邮箱')]:
            widget=QLineEdit();widget.setAccessibleName(label)
            widget.setText((alias or '默认取货人') if key=='alias' else getattr(identity,key,'') if identity else '')
            if key=='document_number':
                if identity:widget.setText(identity.document_number[-4:].upper())
                widget.setEchoMode(QLineEdit.EchoMode.Password)
                widget.setPlaceholderText('四位数字，末位可为 X')
                # Do not use maxLength(4): pasting a full ID would silently keep
                # its first four characters rather than rejecting the input.
            self.fields[key]=widget;form.addRow(label,widget)
        layout.addLayout(form)
        self.import_button=QPushButton('从 TXT 导入取货资料…');self.import_button.clicked.connect(self.import_text);layout.addWidget(self.import_button)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('使用此取货人')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept_checked);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def import_text(self):
        path,_=QFileDialog.getOpenFileName(self,'选择本机取货资料 TXT','','文本文件 (*.txt)')
        if not path:return
        try:
            fields=read_private_text(path)
            contact={k:v for k,v in fields.items() if k in self.fields}
            if not contact:raise ConfigError('TXT 需包含姓氏、名字、证件后四位、手机号或邮箱字段')
            for key,value in contact.items():self.fields[key].setText(value)
        except ConfigError as exc:QMessageBox.information(self,'TXT 导入',str(exc))
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
        self.session_profiles={}
        l=self.page('取货人','直接填写姓名、证件后四位、手机号和邮箱，本次运行使用，不访问钥匙串。')
        frame,c=self.card();self.label(c,'官网实测：姓氏 + 名字 + 联系方式 + 证件后四位','eyebrow');self.label(c,'证件只填写后四位，末位 X 也可填写。出现手机号验证时在官网填写验证码。','muted');l.addWidget(frame)
        self.profile_table=self.table(['显示别名','默认取货人','存储位置']);self.profile_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);l.addWidget(self.profile_table,1)
        row=QHBoxLayout();self.button(row,'填写取货人',self.add_profile,'primary');self.button(row,'编辑 / 重新填写',self.edit_profile);self.button(row,'设为默认',self.default_profile);self.button(row,'移除',self.delete_profile);l.addLayout(row)
        self.label(l,'旧资料保留，不自动读取。选中旧条目可直接重新填写；退出后，本次输入不会写入配置或日志。','muted')

    def refresh_profiles(self):
        hidden={p['source_id'] for p in self.session_profiles.values() if p['source_id']}
        self.profiles=[p for p in self.repo.profiles() if p['id'] not in hidden]+list(self.session_profiles.values())
        self.profile_table.setRowCount(len(self.profiles))
        for row,p in enumerate(self.profiles):
            location='仅本次使用' if IdentityVault.cached(p['id']) is not None else '旧资料 · 请重新填写'
            for col,value in enumerate([p['alias'],'默认' if p['id']==self.config.profile_id else '',location]):self.profile_table.setItem(row,col,QTableWidgetItem(value))

    def can_edit_profile(self):
        if self.worker and self.worker.isRunning() and not self.worker.idle:
            self.message_label.setText('请先停止当前任务，再填写或修改取货资料。');return False
        return True

    def store_profile(self,dialog,profile_id):
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            for field in dialog.fields.values():field.clear()
            dialog.deleteLater();return
        new_id=str(uuid4())
        try:
            identity=dialog.identity();self.redactor.register(identity)
            saved=settings_for_disk(self.config)
            IdentityVault.remember(new_id,identity,fallback={k:saved[k] for k in ('profile_id','transmit_identity')})
            updated=deepcopy(self.config);updated.profile_id=new_id;updated.transmit_identity=True
            try:self.repo.save_config(updated)
            except Exception:
                IdentityVault.forget(new_id);raise
            previous=self.session_profiles.pop(profile_id,None)
            source_id=previous['source_id'] if previous else profile_id if any(p['id']==profile_id for p in self.repo.profiles()) else ''
            self.session_profiles[new_id]={'id':new_id,'alias':dialog.fields['alias'].text().strip(),'source_id':source_id}
            if previous:IdentityVault.forget(profile_id)
            self.config=updated;self.transmit.setChecked(True);self.refresh_profiles();self.refresh_preparation()
            self.message_label.setText('取货资料本次可用，不读写钥匙串；退出后需重新填写或导入 TXT。')
        except (FlowError,ConfigError) as exc:
            IdentityVault.forget(new_id);QMessageBox.information(self,'取货人',str(exc))
        finally:
            for field in dialog.fields.values():field.clear()
            dialog.deleteLater()

    def add_profile(self):
        if self.can_edit_profile():self.store_profile(ProfileDialog(self),'')

    def edit_profile(self):
        row=self.profile_table.currentRow()
        if row<0 or not self.can_edit_profile():return
        p=self.profiles[row]
        self.store_profile(ProfileDialog(self,IdentityVault.cached(p['id']),p['alias']),p['id'])

    def default_profile(self):
        row=self.profile_table.currentRow()
        if row<0 or not self.can_edit_profile():return
        if IdentityVault.cached(self.profiles[row]['id']) is None:self.edit_profile();return
        self.config.profile_id=self.profiles[row]['id'];self.repo.save_config(self.config);self.refresh_profiles();self.refresh_preparation()

    def delete_profile(self):
        row=self.profile_table.currentRow()
        if row<0 or not self.can_edit_profile():return
        p=self.profiles[row]
        saved=settings_for_disk(self.config)
        if p['id'] in self.session_profiles:
            self.session_profiles.pop(p['id']);IdentityVault.forget(p['id'])
            if self.config.profile_id==p['id']:
                self.config.profile_id=saved['profile_id'];self.config.transmit_identity=saved['transmit_identity']
        else:
            self.repo.remove_alias(p['id'])
            if self.config.profile_id==p['id']:self.config.profile_id='';self.config.transmit_identity=False
        self.transmit.setChecked(self.config.transmit_identity);self.repo.save_config(self.config);self.refresh_profiles();self.refresh_preparation()
