from __future__ import annotations
import textwrap
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
import json
import math
import time
from PySide6.QtCore import Qt,QTimer,Slot,QDateTime,QDate,QTime,QTimeZone,QSize,QLocale,QEventLoop
from PySide6.QtGui import QColor,QIcon,QPainter,QPixmap
from PySide6.QtWidgets import (QMainWindow,QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,QLayout,
 QFormLayout,QStackedWidget,QScrollArea,QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox,QCheckBox,QDateTimeEdit,
 QTableWidget,QTableWidgetItem,QHeaderView,QListWidget,QListWidgetItem,QAbstractItemView,QPlainTextEdit,QTabWidget,
 QFileDialog,QMessageBox,QSystemTrayIcon,QProgressBar,QDialog,QDialogButtonBox,QSizePolicy,QApplication)
from app import __version__
from app.models.config import Settings,Product,Store,Identity,Mode,State,STATE_LABELS,ConfigError
from app.models.flow import FlowError
from app.services.storage import Repository
from app.services.simulation import SCENARIOS
from app.security.keychain import IdentityVault, CredentialVault
from app.gui.account import AccountDialog
from app.security.privacy import Redactor
from app.gui.worker import Worker
from app.gui.payment import PaymentDialog
from app.gui.payment_settings import PaymentChoices
from app.gui.product_settings import ProductChoices
from app.services.catalog import CatalogCache,groups_from_controls,normalize_name,preorder_time
from app.services.traffic import SiteCooldown
from app.services.payment import PaymentStore
from app.utils.paths import resource_path
from app.utils.scheduling import wall_time,schedule_preview
from app.services.readiness import preparation_items,PreparationItem
from app.gui.preparation import PreparationDialog
from app.gui.widgets import outline_icon,divider,section_header
from app.services.consent import NOTICE_TITLE, NOTICE_INTRO, NOTICE_CHOICES, NOTICE_VERSION, PRIVACY_POLICY_URL
from app.services.stores import StoreDirectory
from app.services.network_diagnosis import FAMILY_LABELS
from app.services.network_state import network_summary
from app.services.daily_chrome import EXTENSION_URL
from app.utils.qt import single_shot

class ProfileDialog(QDialog):
    def __init__(self,parent=None,identity=None,alias=''):
        super().__init__(parent);self.setWindowTitle('取货人');self.resize(480,350)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,18,20,18);layout.setSpacing(14)
        note=QLabel('Apple 取货只需证件后四位，不需要完整身份证号码。\n取货信息仅保存到 macOS 钥匙串。');note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
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

class MainWindow(QMainWindow):
    def __init__(self,root:Path):
        super().__init__();self.root=root;self.redactor=Redactor();self.repo=Repository(root/'autobuyer.sqlite3',self.redactor)
        self.worker=None;self.closing=False;self.catalog_cache=CatalogCache(root);self.catalog=self.catalog_cache.load();self.store_results=[];self.run_events=[]
        self.embedded_browser=None;self.pending_browser_action=None
        self.store_directory=StoreDirectory()
        self.payment_store=PaymentStore(root);self.payment_dialog=None
        self.preparation_dialog=None;self.browser_installed=None;self.login_observation=None;self.session_open=False
        self.editor_timezone='Asia/Shanghai';self.cooldown_until=time.monotonic()+SiteCooldown(root).remaining();self.last_state='';self.preparation_refresh_at=0.
        self.price_dialog=None;self.consent_dialog=None;self.pending_consent=None
        try:self.config=self.repo.load_config()
        except ConfigError:self.config=Settings()
        self.setWindowTitle('Apple AutoBuyer');self.resize(1280,825);self.setMinimumSize(1060,720);self.setLocale(QLocale('zh_CN'))
        self.setStyleSheet(resource_path('gui/theme.qss').read_text(encoding='utf-8'))
        self.setWindowIcon(self.icon())
        central=QWidget();self.setCentralWidget(central);outer=QHBoxLayout(central);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        sidebar=QFrame();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(210);side=QVBoxLayout(sidebar);side.setContentsMargins(14,24,12,18);side.setSpacing(4)
        brand=QHBoxLayout();brand.setContentsMargins(8,0,0,0);brand.setSpacing(10)
        mark=QLabel();mark.setPixmap(self.icon().pixmap(QSize(36,36)));brand.addWidget(mark,0,Qt.AlignmentFlag.AlignTop)
        brand_text=QVBoxLayout();brand_text.setSpacing(4);self.label(brand_text,'AutoBuyer','brand');self.label(brand_text,'Apple 中国大陆','muted');brand.addLayout(brand_text);brand.addStretch();side.addLayout(brand);side.addSpacing(28)
        self.stack=QStackedWidget();self.nav=[]
        for i,title in enumerate(['商品配置','自提门店','取货人','付款与授权','控制台','运行记录','官网浏览器']):
            if i in (0,4):
                if i==4:side.addSpacing(16)
                self.label(side,'购买设置' if i==0 else '运行','sectionTitle')
            b=QPushButton(title);b.setObjectName('nav');b.setIcon(outline_icon(['box','store','person','card','sliders','document','browser'][i]));b.setIconSize(QSize(18,18));b.setCheckable(True);b.setAutoExclusive(True);b.toggled.connect(lambda checked,index=i:self.stack.setCurrentIndex(index) if checked else None);side.addWidget(b);self.nav.append(b)
        side.addStretch();self.label(side,f'v{__version__}','muted')
        outer.addWidget(sidebar);outer.addWidget(self.stack,1)
        self.build_product();self.build_stores();self.build_profiles();self.build_payments();self.build_console();self.build_logs();self.build_browser()
        self.stack.currentChanged.connect(self.page_changed);self.nav[4].setChecked(True);self.stack.setCurrentIndex(4)
        self.populate();self.refresh_profiles();self.refresh_logs()
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(1000);self.tick()
        self.tray=QSystemTrayIcon(self.icon(),self);self.tray.setToolTip('Apple AutoBuyer');self.tray.activated.connect(lambda _:self.showNormal());self.tray.show()

    def icon(self):
        p=QPixmap(64,64);p.fill(Qt.GlobalColor.transparent);painter=QPainter(p);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setBrush(QColor('#007aff'));painter.setPen(Qt.PenStyle.NoPen);painter.drawRoundedRect(2,2,60,60,13,13);painter.setPen(QColor('#ffffff'));font=painter.font();font.setPointSize(27);font.setBold(True);painter.setFont(font);painter.drawText(p.rect(),Qt.AlignmentFlag.AlignCenter,'A');painter.end();return QIcon(p)
    def label(self,layout,text,object_name=None):
        l=QLabel(text);l.setWordWrap(True);l.setTextFormat(Qt.TextFormat.PlainText)
        if object_name:l.setObjectName(object_name)
        layout.addWidget(l);return l
    def button(self,layout,text,slot,kind=None):
        b=QPushButton(text);b.setAccessibleName(text)
        if kind:b.setObjectName(kind)
        b.clicked.connect(slot);layout.addWidget(b);return b
    def page(self,title,subtitle):
        page=QWidget();page.setObjectName('contentPage');layout=QVBoxLayout(page);layout.setContentsMargins(24,18,24,16);layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
        if title:self.label(layout,title,'title')
        if subtitle:self.label(layout,subtitle,'subtitle')
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page);self.stack.addWidget(scroll);return layout
    def card(self):
        frame=QFrame();frame.setObjectName('card');layout=QVBoxLayout(frame);layout.setContentsMargins(14,12,14,12);layout.setSpacing(10);return frame,layout
    def table(self,columns):
        t=QTableWidget(0,len(columns));t.setHorizontalHeaderLabels(columns);t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);t.verticalHeader().setVisible(False);t.setAlternatingRowColors(True);t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);return t
    def page_changed(self,index):
        self.nav[index].setChecked(True)
        for i,button in enumerate(self.nav):button.setIcon(outline_icon(['box','store','person','card','sliders','document','browser'][i],'#007aff' if i==index else '#3a3a3c'))

    def build_product(self):
        l=self.page('选择商品','选机型、颜色和容量，官网入口由软件准备。')
        row=QHBoxLayout();source=self.label(row,'型号与配置来自 Apple 中国大陆官网','muted');source.setWordWrap(False);row.addStretch();self.button(row,'更新官网型号与配置',lambda:self.launch('catalog'));l.addLayout(row)
        frame,c=self.card();self.product_choices=ProductChoices(self.catalog);c.addWidget(self.product_choices)
        self.name=self.product_choices.model;self.quantity=self.product_choices.quantity
        self.url=QLineEdit(self);self.url.hide();self.name.currentIndexChanged.connect(self.catalog_selected)
        self.no_trade=QCheckBox('不折抵换购');self.no_care=QCheckBox('不加 AppleCare+');row=QHBoxLayout();row.addWidget(self.no_trade);row.addWidget(self.no_care);row.addStretch();c.addLayout(row);l.addWidget(frame)
        self.label(l,'新机型还没有购买入口时，可先保存计划。软件会从官网目录识别入口并等待开售。','muted');l.addStretch()
        row=QHBoxLayout();self.button(row,'同步当前机型',lambda:self.launch('options'));row.addStretch();self.button(row,'保存商品',self.save,'primary');l.addLayout(row)

    def build_stores(self):
        l=self.page('自提门店','提前选门店，再拖动排列优先级。新机型未开售也可以保存。')
        frame,c=self.card();row=QHBoxLayout()
        self.store_city=QComboBox();self.store_city.setAccessibleName('筛选门店城市');self.store_city.addItem('所有城市','')
        for city in dict.fromkeys(s['city'] for s in self.store_directory.rows):self.store_city.addItem(city,city)
        self.store_city.currentIndexChanged.connect(self.filter_stores);row.addWidget(self.store_city)
        self.search=QLineEdit();self.search.setPlaceholderText('搜索门店、城市或地址');self.search.setAccessibleName('搜索内置门店');self.search.textChanged.connect(self.filter_stores);row.addWidget(self.search,1)
        self.button(row,'添加选中门店',self.add_selected_store);c.addLayout(row)
        self.directory_label=self.label(c,f'内置 Apple 中国大陆 {len(self.store_directory.rows)} 家直营店 · 官网核对 {self.store_directory.checked_at[:10]}','muted')
        self.available_stores=self.table(['门店名称','城市','地址','上次官网供货结果']);self.available_stores.setAccessibleName('内置 Apple 中国大陆门店目录')
        self.available_stores.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.available_stores.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.available_stores.setMinimumHeight(180);self.available_stores.setMaximumHeight(245);self.available_stores.itemDoubleClicked.connect(lambda _:self.add_selected_store());c.addWidget(self.available_stores);l.addWidget(frame)
        self.label(l,'已选门店 · 勾选启用，拖动排序','eyebrow')
        self.store_list=QListWidget();self.store_list.setAccessibleName('已选自提门店优先级');self.store_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove);self.store_list.setDefaultDropAction(Qt.DropAction.MoveAction);self.store_list.setMinimumHeight(120);l.addWidget(self.store_list,1)
        row=QHBoxLayout();self.button(row,'移除选中',self.remove_store);self.other=QCheckBox('允许所选城市的其他门店备用');row.addWidget(self.other);self.no_stock=QComboBox();self.no_stock.addItem('无货时等待','wait');self.no_stock.addItem('无货时停止','stop');row.addWidget(self.no_stock);self.button(row,'检查所选商品库存',lambda:self.launch('stores'));l.addLayout(row)
        self.label(l,'目录只用于提前选择。开售后按所选城市读取官网供货，结账时再次核对；目录不代表有货。','muted')
        row=QHBoxLayout();self.pickup_from=QLineEdit();self.pickup_from.setPlaceholderText('最早 YYYY-MM-DD，可留空');self.pickup_to=QLineEdit();self.pickup_to.setPlaceholderText('最晚 YYYY-MM-DD，可留空');row.addWidget(QLabel('可接受取货日期'));row.addWidget(self.pickup_from);row.addWidget(self.pickup_to);self.button(row,'保存门店设置',self.save,'primary');l.addLayout(row)

    def build_profiles(self):
        l=self.page('取货人','证件、手机号和邮箱保存在 macOS 钥匙串，本机记录只保留显示别名。')
        frame,c=self.card();self.label(c,'官网实测：姓氏 + 名字 + 联系方式 + 证件后四位','eyebrow');self.label(c,'证件只填写后四位，末位 X 也可填写。出现手机号验证时在官网填写验证码。','muted');l.addWidget(frame)
        self.profile_table=self.table(['显示别名','默认取货人','存储位置']);self.profile_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);l.addWidget(self.profile_table,1)
        row=QHBoxLayout();self.button(row,'添加取货人',self.add_profile,'primary');self.button(row,'编辑',self.edit_profile);self.button(row,'设为默认',self.default_profile);self.button(row,'删除',self.delete_profile);l.addLayout(row)
        self.label(l,'先运行官网试跑也可以不保存身份；软件会到取货人页面暂停，允许你直接在官网填写。','muted')

    def build_payments(self):
        l=self.page('付款与授权','直接选择付款方案，勾选需要的授权。')
        frame,c=self.card();c.addLayout(section_header('结账登录策略'))
        self.checkout_strategy=QComboBox();self.checkout_strategy.setAccessibleName('结账登录策略')
        for title,value in [('优先 Apple 账户，游客备用（推荐）','account_then_guest'),('只用 Apple 账户，验证后继续','account_only'),('无需预登录，出现入口时用游客','guest')]:self.checkout_strategy.addItem(title,value)
        c.addWidget(self.checkout_strategy)
        self.label(c,'已有登录直接继续。游客仍可能要求手机验证码；届时保留购物袋，提醒你完成验证。','muted');l.addWidget(frame)
        row=QHBoxLayout();row.setSpacing(20)
        frame,c=self.card();c.addLayout(section_header('付款方案'));c.addWidget(divider())
        self.payment_choices=PaymentChoices();self.installment=self.payment_choices.terms
        f=QFormLayout();f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow);f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        f.addRow('首选付款方式',self.payment_choices.primary);f.addRow('分期期数',self.installment);c.addLayout(f)
        self.label(c,'备用付款方式','eyebrow');c.addWidget(self.payment_choices.backups)
        self.label(c,'勾选需要的备用方式，可拖动排序。官网会重新确认可用方式与期数。','muted');row.addWidget(frame,1)
        frame,c=self.card();c.addLayout(section_header('填写与下单'));c.addWidget(divider())
        self.transmit=QCheckBox('自动填写所选取货人');self.transmit.setAccessibleName('允许向 Apple 中国大陆官网填写所选 Keychain 取货人信息');c.addWidget(self.transmit)
        self.label(c,'向 Apple 中国大陆官网填写钥匙串中的姓名、联系方式和证件字段。','muted');c.addWidget(divider())
        self.auto=QCheckBox('条件满足后自动提交订单');self.auto.setAccessibleName('允许在预设条件满足时自动提交真实订单');c.addWidget(self.auto)
        self.label(c,'仅用于真实购买。商品、数量、金额、门店和付款方案须通过最终核对；付款由你完成。','muted');c.addStretch()
        self.label(c,'自动提交目前只支持招商银行 24 期；官网缺少最终门店信息时，仍会请你核对。','muted')
        self.label(c,'官网模拟下单会在最终核对时停止。','muted');row.addWidget(frame,1);l.addLayout(row)
        frame,c=self.card();c.addLayout(section_header('Apple 隐私声明 · 提前同意'));c.addWidget(divider())
        self.label(c,'开售前勾选下面两项并保存，遇到相同声明时自动同意并继续。','muted')
        self.label(c,NOTICE_TITLE,'eyebrow')
        self.label(c,'\n'.join(NOTICE_INTRO),'muted')
        self.privacy_policy=QCheckBox(NOTICE_CHOICES[0]);self.privacy_policy.setAccessibleName(NOTICE_CHOICES[0]);c.addWidget(self.privacy_policy)
        self.privacy_transfer=QCheckBox('\n'.join(textwrap.wrap(NOTICE_CHOICES[1],width=54)))
        self.privacy_transfer.setAccessibleName(NOTICE_CHOICES[1]);self.privacy_transfer.setMinimumHeight(44);c.addWidget(self.privacy_transfer)
        policy_link=QLabel(f'<a href="{PRIVACY_POLICY_URL}">查看 Apple 隐私政策 ↗</a>');policy_link.setOpenExternalLinks(True);c.addWidget(policy_link)
        self.label(c,'保存后重启仍有效。官网声明内容变化时暂停，让你查看新内容。','muted')
        self.consent_status=self.label(c,'当前没有待确认的官网协议。','muted')
        self.consent_entry=self.button(c,'查看并确认当前协议',self.open_consent);self.consent_entry.setEnabled(False);l.addWidget(frame)
        l.addStretch();row=QHBoxLayout();row.addStretch();self.button(row,'创建新的购买计划',self.new_plan);self.button(row,'保存付款与授权',self.save,'primary');l.addLayout(row)

    def build_console(self):
        l=self.page(None,None);l.setSpacing(12)
        row=QHBoxLayout();self.label(row,'控制台','title');row.addStretch()
        self.preparation_button=self.button(row,'准备检查…',self.show_preparation)
        self.preparation_summary=QLabel(self);self.preparation_summary.hide()
        self.start_website=self.button(row,'官网模拟下单',lambda:self.launch('website'),'primary')
        self.start_live=self.button(row,'开始抢购',lambda:self.launch('live'))
        l.addLayout(row);l.addWidget(divider())
        row=QHBoxLayout();row.setSpacing(16);self.metrics={}
        self.state_dot=QLabel('●');self.state_dot.setObjectName('stateDot');row.addWidget(self.state_dot)
        self.metrics['stage']=self.label(row,'尚未开始','stageValue')
        self.message_label=self.label(row,'保存配置后，先登录并试跑当前商品。','taskMessage');row.setStretch(2,1)
        l.addLayout(row);l.addWidget(divider())

        work=QHBoxLayout();work.setSpacing(24)
        left=QWidget();c=QVBoxLayout(left);c.setContentsMargins(0,4,0,0);c.setSpacing(8)
        c.addLayout(section_header('购买计划','编辑配置',lambda:self.stack.setCurrentIndex(0)));c.addWidget(divider())
        product_row=QHBoxLayout();product_row.setContentsMargins(16,8,12,8);product_row.setSpacing(16)
        phone=QLabel();phone.setPixmap(outline_icon('phone',size=38).pixmap(QSize(38,38)));product_row.addWidget(phone)
        product_text=QVBoxLayout();product_text.setSpacing(4)
        self.plan_product=self.label(product_text,'尚未设置商品','productTitle')
        self.plan_details=self.label(product_text,'请先保存商品配置','muted');product_row.addLayout(product_text,1);c.addLayout(product_row);c.addWidget(divider())
        self.plan_budget=QLabel();self.plan_stores=QLabel();self.plan_payment=QLabel()
        for name,value in [('官网价格',self.plan_budget),('自提优先级',self.plan_stores),('付款方案',self.plan_payment)]:
            row=QHBoxLayout();row.setContentsMargins(16,5,8,5);row.setSpacing(16)
            label=QLabel(name);label.setMinimumWidth(180);row.addWidget(label,0,Qt.AlignmentFlag.AlignTop)
            value.setTextFormat(Qt.TextFormat.PlainText);value.setWordWrap(True);row.addWidget(value,1);c.addLayout(row);c.addWidget(divider())
        self.target_label=QLabel(self);self.target_label.hide()
        c.addSpacing(14);c.addLayout(section_header('开售与检查'));c.addWidget(divider())
        self.schedule_enabled=QCheckBox('按开售时间运行');c.addWidget(self.schedule_enabled)
        self.schedule=QDateTimeEdit();self.schedule.setTimeZone(QTimeZone.utc())
        self.schedule.setDateTime(self.editor_datetime(datetime.now(ZoneInfo(self.editor_timezone))))
        self.schedule.setCalendarPopup(True);self.schedule.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.timezone=QLabel('北京时间')
        self.timezone.setToolTip('开售时间和运行记录统一使用北京时间。')
        row=QHBoxLayout();row.setSpacing(14);row.addWidget(self.schedule,1);row.addWidget(self.timezone);c.addLayout(row)
        self.official_time_button=self.button(c,'使用官网开售时间',self.use_official_time,'link');self.official_time_button.hide()
        self.interval=QDoubleSpinBox();self.interval.setRange(10,600);self.interval.setSuffix(' 秒');self.interval.setValue(10)
        self.wait=QSpinBox();self.wait.setRange(1,1440);self.wait.setSuffix(' 分钟');self.wait.setValue(30)
        self.label(c,'官网入口每 5 秒尝试；加载与排队时保留页面。','muted')
        self.schedule_note=self.label(c,'未预约时立即准备；官网试跑立即执行。','muted')
        self.metrics['countdown']=QLabel(self);self.metrics['countdown'].hide();c.addStretch()
        work.addWidget(left,5)
        vertical=QFrame();vertical.setObjectName('verticalDivider');vertical.setFixedWidth(1);work.addWidget(vertical)
        right=QWidget();r=QVBoxLayout(right);r.setContentsMargins(0,4,0,0);r.setSpacing(12)
        r.addLayout(section_header('软件浏览器'));r.addWidget(divider())
        self.login_label=self.label(r,'登录状态：未检查','sessionStatus')
        self.browser_label=self.label(r,'可接入日常 Chrome，沿用已登录的官网会话。','muted')
        self.browser_kind=QComboBox();self.browser_kind.setAccessibleName('官网浏览器模式');self.browser_kind.addItem('软件内置浏览器','embedded');self.browser_kind.addItem('独立 Chrome · 软件专用会话','chrome');self.browser_kind.addItem('日常 Chrome · 沿用现有登录','daily_chrome');r.addWidget(self.browser_kind)
        row=QHBoxLayout();self.button(row,'接入日常 Chrome',self.connect_daily_chrome);r.addLayout(row)
        row=QHBoxLayout();self.button(row,'登录并保持会话',lambda:self.launch('login'))
        self.button(row,'打开浏览器',self.focus_browser);r.addLayout(row)
        row=QHBoxLayout();self.button(row,'检查登录状态',lambda:self.launch('login_check'))
        self.close_session_button=self.button(row,'关闭会话',self.close_session);self.close_session_button.setEnabled(False);r.addLayout(row)
        self.keep_awake=QCheckBox('防止 Mac 自动睡眠');r.addWidget(self.keep_awake)
        self.keep_awake.setToolTip('建议连接电源。停止任务会保留浏览器会话，关闭会话才会退出浏览器。')
        r.addWidget(divider());self.label(r,'停止任务会保留浏览器会话。','muted');r.addSpacing(8);r.addWidget(divider())
        row=QHBoxLayout();self.button(row,'查看付款二维码',self.show_payment);self.button(row,'提取当前付款码',self.request_payment);r.addLayout(row)
        self.help_button=self.button(r,'使用帮助',self.show_help,'link');self.help_button.setIcon(outline_icon('help','#007aff'))
        self.help_button.setStyleSheet('text-align: left;');r.addStretch()
        work.addWidget(right,3);l.addLayout(work);l.addWidget(divider())

        row=QHBoxLayout();self.label(row,'运行日志','sectionTitle');row.addStretch()
        self.console_consent_entry=self.button(row,'官网隐私确认…',self.open_consent);self.console_consent_entry.hide()
        self.resume_button=self.button(row,'继续检查',self.resume);self.resume_button.setEnabled(False)
        self.stop_button=self.button(row,'停止任务',self.stop,'danger');self.stop_button.setEnabled(False);l.addLayout(row)
        self.progressbar=QProgressBar();self.progressbar.setRange(0,12);self.progressbar.setValue(0);self.progressbar.setTextVisible(False);self.progressbar.hide()
        self.console_log=QPlainTextEdit();self.console_log.setReadOnly(True);self.console_log.setMaximumBlockCount(300)
        self.console_log.setMinimumHeight(115);self.console_log.setPlaceholderText('运行后显示加载、配置、库存与官网提示。');l.addWidget(self.console_log,1)
        self.console_log.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Ignored)
        row=QHBoxLayout();self.banner=self.label(row,'官网试跑会到最终核对页停止。','muted');row.addStretch()
        clock_icon=QLabel();clock_icon.setPixmap(outline_icon('clock','#74747a',16).pixmap(QSize(16,16)));row.addWidget(clock_icon)
        self.clock_caption=self.label(row,'北京时间','muted');self.metrics['clock']=self.label(row,'--:--:--','muted')
        for label in (self.banner,self.clock_caption,self.metrics['clock']):label.setWordWrap(False)
        self.button(row,'诊断…',self.show_diagnostics,'link');l.addLayout(row)

        self.diagnostics_dialog=QDialog(self);self.diagnostics_dialog.setWindowTitle('诊断工具');self.diagnostics_dialog.resize(450,180)
        diagnostic_layout=QVBoxLayout(self.diagnostics_dialog)
        f=QFormLayout();f.addRow('平时检查间隔',self.interval);f.addRow('最长等待',self.wait);diagnostic_layout.addLayout(f)
        self.label(diagnostic_layout,'官网入口持续每 5 秒尝试；加载、排队和服务器指定等待时保留页面。','muted')
        self.label(diagnostic_layout,'检查浏览器环境；本地异常测试使用合成数据。','muted')
        row=QHBoxLayout();self.button(row,'检查环境',lambda:self.launch_diagnostic('environment'));self.button(row,'安装浏览器',lambda:self.launch_diagnostic('install'));row.addStretch();diagnostic_layout.addLayout(row)
        row=QHBoxLayout();self.scenario=QComboBox()
        for key,value in SCENARIOS.items():self.scenario.addItem(value,key)
        row.addWidget(self.scenario,1);self.button(row,'本地异常测试',lambda:self.launch_diagnostic('local'));diagnostic_layout.addLayout(row)

    def show_diagnostics(self):
        self.diagnostics_dialog.show();self.diagnostics_dialog.raise_()

    def launch_diagnostic(self,action):
        self.diagnostics_dialog.hide();self.launch(action)

    def current_zone(self):
        return 'Asia/Shanghai'

    def refresh_plan_summary(self,config=None):
        config=config or self.collect();p=config.product
        self.plan_product.setText(p.name or '尚未设置商品')
        colors={'white':'白色','black':'黑色','starwhite':'星光白色','nightsky':'夜空色','lavender':'薰衣草紫色','mistblue':'青雾蓝色','mist-blue':'青雾蓝色','sage':'鼠尾草绿色'}
        options=[colors.get(v,v.upper() if v.lower().endswith(('gb','tb')) else v) for v in p.options.values()]
        self.plan_details.setText(' · '.join(options+[f'{p.quantity} 件']))
        total=self.product_choices.total();self.plan_budget.setText(f'RMB {float(total):,.2f}（官网公布）' if float(total)>0 else '结账前核对官网价格')
        self.plan_stores.setText('\n'.join(s.name for s in config.stores if s.enabled) or '尚未选择门店')
        self.plan_payment.setText(' → '.join(config.payments)+(f' · {config.installment_months} 期' if config.installment_months else '') or '尚未设置')

    def show_help(self):
        dialog=QDialog(self);dialog.setWindowTitle('使用帮助');dialog.resize(570,430)
        layout=QVBoxLayout(dialog);self.label(layout,'先准备配置，再验证官网','title')
        self.label(layout,'1. 选机型、颜色和容量，从内置目录选择门店并排序。\n2. 在付款页点选结账策略，保存计划。\n3. 在“官网浏览器”保存 Apple 账户，勾选自动登录和信任浏览器，再提前完成首次验证。原有 Chrome 登录可继续使用。\n4. 保持同一浏览器会话；需要验证码时会保留页面并提醒。','helpText')
        layout.addWidget(divider());self.label(layout,'遇到 541 或一直转圈','sectionTitle')
        self.label(layout,'先查看官网浏览器的实际提示。关键请求被拒绝时，软件会保留会话并显示等待秒数；等待结束、处理页面提示后才能继续。不要反复提交密码或订单。配送预估失败与自提查询会分开核验，页面有货仍不代表订单已锁定库存。','helpText')
        layout.addStretch();self.button(layout,'返回控制台',dialog.close);dialog.show();self.help_dialog=dialog

    def build_logs(self):
        l=self.page('运行记录与订单','查看运行阶段、门店变化和各订单对应的付款码。付款是否完成仍以官网为准。')
        self.record_tabs=QTabWidget();l.addWidget(self.record_tabs,1)
        self.log_table=self.table(['本地时间','购买阶段','结果','累计秒']);self.log_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.record_tabs.addTab(self.log_table,'运行阶段')
        stock=QWidget();stock_layout=QVBoxLayout(stock);self.label(stock_layout,'这是每次官网实际读取的历史变化；旧结果不代表现在有货。','muted');self.stock_table=self.table(['检测时间','商品与配置','门店','变化','官网说明']);self.stock_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);stock_layout.addWidget(self.stock_table);self.record_tabs.addTab(stock,'门店变化')
        orders=QWidget();orders_layout=QVBoxLayout(orders);self.label(orders_layout,'订单创建、付款和取货分别确认。付款码只对应所选订单，到期不会重新计时。','muted');self.orders_table=self.table(['订单尾号','提交结果','付款码记录','最近记录（本地时间）']);self.orders_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);orders_layout.addWidget(self.orders_table);self.button(orders_layout,'查看所选订单付款码',self.show_selected_payment);self.record_tabs.addTab(orders,'订单与付款码')
        row=QHBoxLayout();self.button(row,'刷新记录',self.refresh_logs);self.button(row,'导出脱敏日志',self.export_logs);self.button(row,'继续当前浏览器',self.resume);self.button(row,'恢复检查 / 重新准备',self.recover);l.addLayout(row)
        self.label(l,'恢复会重新验证页面与配置；已提交或结果未知的购买计划只允许核对，不重新提交。停止程序不会自动取消已有订单。','muted')

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
        self.account_status.setText('已保存到钥匙串 · '+('自动登录已开启' if self.config.auto_login else '自动登录未开启')+(' · 已预选信任此浏览器' if self.config.auto_login and self.config.trust_browser else '') if saved else '尚未保存 Apple 账户')
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
                self.message_label.setText('Apple 账户已保存到本机钥匙串。请提前登录并完成首次验证。')
                if old_id:vault.delete(old_id)
            finally:
                dialog.password.clear();dialog.previous=None;dialog.deleteLater()
        except (FlowError,ConfigError) as exc:QMessageBox.information(self,'Apple 账户',str(exc))

    def remove_account(self):
        if self.worker and self.worker.isRunning() and not self.worker.idle:
            self.message_label.setText('请先停止当前任务，再删除保存的 Apple 账户。');return
        if not self.config.credential_id:return
        if QMessageBox.question(self,'删除保存账号','删除本机钥匙串中的 Apple 账户和密码？浏览器里的现有登录仍然保留。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
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

    def populate(self):
        self.update_account_status()
        p=self.config.product;self.product_choices.set_product(p);self.catalog_selected(self.name.currentIndex());self.url.setText(p.url or (self.name.currentData() or {}).get('url',''));self.no_trade.setChecked(p.exclude_trade_in);self.no_care.setChecked(p.exclude_applecare)
        self.store_list.clear()
        for store in self.config.stores:self.append_store(self.store_directory.enrich(store))
        self.render_store_directory()
        self.other.setChecked(self.config.allow_other_stores);self.no_stock.setCurrentIndex(self.no_stock.findData(self.config.no_stock));self.pickup_from.setText(self.config.pickup_from);self.pickup_to.setText(self.config.pickup_to)
        self.keep_awake.setChecked(self.config.keep_awake)
        self.checkout_strategy.setCurrentIndex(self.checkout_strategy.findData(self.config.checkout_strategy))
        self.browser_kind.setCurrentIndex(self.browser_kind.findData(self.config.browser_kind))
        self.payment_choices.populate(self.config.payments,self.config.installment_months);self.auto.setChecked(self.config.auto_submit);self.transmit.setChecked(self.config.transmit_identity);self.interval.setValue(max(10,self.config.interval));self.wait.setValue(int(self.config.max_wait/60))
        known_notice=self.config.privacy_notice_version==NOTICE_VERSION
        self.privacy_policy.setChecked(known_notice and self.config.privacy_policy_accepted)
        self.privacy_transfer.setChecked(known_notice and self.config.privacy_transfer_acknowledged)
        self.editor_timezone=self.current_zone()
        self.schedule_enabled.setChecked(bool(self.config.starts_at))
        if self.config.starts_at:
            dt=datetime.fromisoformat(self.config.starts_at).astimezone(ZoneInfo(self.current_zone()));self.schedule.setDateTime(self.editor_datetime(dt))
        else:self.schedule.setDateTime(self.editor_datetime(datetime.now(ZoneInfo(self.current_zone()))))
        self.scenario.setCurrentIndex(max(0,self.scenario.findData(self.config.scenario)))
        self.target_label.setText(f'{p.name} · 数量 {p.quantity} · 最大总金额 RMB {p.max_total}')
        self.refresh_plan_summary(self.config)
    def append_option(self,key,value,choices=None):
        offer=deepcopy(self.product_choices.offer);options=self.product_choices.selected_options();options[key]=value
        if choices:
            groups=groups_from_controls([dict(c,name=key,kind='radio') for c in choices]);offer['groups']=[g for g in offer.get('groups',[]) if g['key']!=key]+groups
        self.product_choices.show_offer(offer,options)
    def append_store(self,store):
        item=QListWidgetItem(f'{store.name}  ·  {store.city}');item.setData(Qt.ItemDataRole.UserRole,store);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsDragEnabled);item.setCheckState(Qt.CheckState.Checked if store.enabled else Qt.CheckState.Unchecked);self.store_list.addItem(item)
    def collect(self):
        c=deepcopy(self.config);options=self.product_choices.selected_options()
        c.product=Product(self.name.currentText().strip(),self.url.text().strip(),options,self.quantity.value(),self.product_choices.total(),'中国大陆',self.no_trade.isChecked(),self.no_care.isChecked(),price_from_website=True,discover_from_store=True)
        c.stores=[]
        for row in range(self.store_list.count()):
            item=self.store_list.item(row);store=self.store_directory.enrich(item.data(Qt.ItemDataRole.UserRole));store.enabled=item.checkState()==Qt.CheckState.Checked;c.stores.append(store)
        c.payments=self.payment_choices.selected();c.installment_months=self.installment.currentData();c.auto_submit=self.auto.isChecked();c.transmit_identity=self.transmit.isChecked();c.allow_other_stores=self.other.isChecked();c.no_stock=self.no_stock.currentData();c.pickup_from=self.pickup_from.text().strip();c.pickup_to=self.pickup_to.text().strip();c.interval=self.interval.value();c.max_interval=max(60,c.interval);c.max_wait=self.wait.value()*60;c.scenario=self.scenario.currentData();c.display_timezone=self.current_zone()
        c.keep_awake=self.keep_awake.isChecked()
        c.checkout_strategy=self.checkout_strategy.currentData();c.browser_kind=self.browser_kind.currentData()
        c.privacy_policy_accepted=self.privacy_policy.isChecked();c.privacy_transfer_acknowledged=self.privacy_transfer.isChecked()
        c.privacy_notice_version=NOTICE_VERSION if c.privacy_policy_accepted or c.privacy_transfer_acknowledged else ''
        if self.schedule_enabled.isChecked():
            dt=wall_time(self.schedule.dateTime().toPython(),c.display_timezone);c.starts_at=dt.isoformat()
        else:c.starts_at=''
        return c
    @Slot()
    def save(self):
        try:self.config=self.collect();self.repo.save_config(self.config);self.target_label.setText(f'{self.config.product.name} · {self.config.product.quantity} 件 · 最大总金额 RMB {self.config.product.max_total}');self.message_label.setText('配置已保存到本机')
        except (ConfigError,ValueError) as exc:QMessageBox.information(self,'请检查配置',str(exc))
    def load_stocked_example(self):
        self.config.product=Product(name='iPhone 17',url='https://www.apple.com.cn/shop/buy-iphone/iphone-17',options={'dimensionColor':'white','dimensionCapacity':'256gb'},max_total='7000.00')
        self.config.stores=[Store('Apple 南京东路',store_id='R359'),Store('Apple 上海环贸 iapm',store_id='R401')]
        self.config.payments=['招商银行'];self.config.installment_months=24
        self.config.auto_submit=False;self.config.starts_at='';self.config.transmit_identity=False;self.populate();self.message_label.setText('载入 2026-10-11 官网已观察配置；试跑时会重新验证价格、控件和库存。');self.stack.setCurrentIndex(0)
    def new_plan(self):
        if self.worker and self.worker.isRunning() and not self.worker.idle:
            self.message_label.setText('请先停止当前购买任务，再创建新计划。');return
        try:
            self.config=self.collect();self.config.intent_id=str(uuid4());self.config.auto_submit=False;self.auto.setChecked(False)
            self.repo.save_config(self.config);self.message_label.setText('已创建新购买计划，请重新设置真实下单授权；以前的订单和提交锁保留。')
        except (ConfigError,ValueError) as exc:QMessageBox.information(self,'购买计划',str(exc))
    @Slot(int)
    def catalog_selected(self,index):
        offer=self.name.currentData() or {};self.url.setText(offer.get('url',''))
        if hasattr(self,'official_time_button'):
            target=preorder_time(offer.get('preorder_note',''))
            self.official_time_button.setVisible(bool(target and target>datetime.now(timezone.utc)))

    def use_official_time(self):
        target=preorder_time(self.product_choices.offer.get('preorder_note',''))
        if target and target>datetime.now(timezone.utc):
            self.schedule.setDateTime(self.editor_datetime(target.astimezone(ZoneInfo(self.current_zone()))));self.schedule_enabled.setChecked(True);self.tick()
            self.message_label.setText('已按官网公布的时间设置；会在开售前复核登录，并等待购买开放。')
    def render_store_directory(self):
        observations={s['store_id']:s for s in self.store_results}
        rows=[*self.store_directory.rows,*[s for s in self.store_results if s['store_id'] not in self.store_directory.by_id]]
        self.available_stores.setRowCount(len(rows))
        for index,s in enumerate(rows):
            observed=observations.get(s['store_id'])
            status=('可选 · ' if observed['available'] else '不可选 · ')+observed['quote'] if observed else '尚未查询'
            values=[s['name'],s['city'],s.get('address','官网新门店'),status]
            if observed:values[-1]+=' · '+observed.get('observed_at','')
            for col,value in enumerate(values):
                item=QTableWidgetItem(value);item.setData(Qt.ItemDataRole.UserRole,s);self.available_stores.setItem(index,col,item)
        self.filter_stores()
    def filter_stores(self,*_):
        term=self.search.text().strip().lower();city=self.store_city.currentData()
        for row in range(self.available_stores.rowCount()):
            data=self.available_stores.item(row,0).data(Qt.ItemDataRole.UserRole)
            text=' '.join(str(data.get(k,'')) for k in ('name','city','address')).lower()
            self.available_stores.setRowHidden(row,bool((city and city!=data['city']) or term not in text))
    def add_selected_store(self):
        added=0
        for index in sorted({i.row() for i in self.available_stores.selectionModel().selectedRows()}):
            if self.available_stores.isRowHidden(index):continue
            data=self.available_stores.item(index,0).data(Qt.ItemDataRole.UserRole)
            if any(self.store_list.item(i).data(Qt.ItemDataRole.UserRole).store_id==data['store_id'] for i in range(self.store_list.count())):continue
            if data['store_id'] in self.store_directory.by_id:store=self.store_directory.store(data)
            else:store=Store(data['name'],data['city'],store_id=data['store_id'],province=data.get('province',''),district=data.get('district',''))
            self.append_store(store);added+=1
        if added:self.message_label.setText(f'已添加 {added} 家门店，请保存门店设置。')
    def remove_store(self):
        row=self.store_list.currentRow()
        if row>=0:self.store_list.takeItem(row)
    def refresh_profiles(self):
        self.profiles=self.repo.profiles();self.profile_table.setRowCount(len(self.profiles))
        for row,p in enumerate(self.profiles):
            for col,value in enumerate([p['alias'],'默认' if p['id']==self.config.profile_id else '','macOS Keychain']):self.profile_table.setItem(row,col,QTableWidgetItem(value))
    def store_profile(self,dialog,profile_id):
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        try:
            identity=dialog.identity();self.redactor.register(identity);IdentityVault().put(profile_id,identity);self.repo.save_alias(profile_id,dialog.fields['alias'].text().strip())
            if not self.config.profile_id:self.config.profile_id=profile_id
            self.repo.save_config(self.config);self.refresh_profiles()
        except FlowError as exc:QMessageBox.information(self,'Keychain',str(exc))
    def add_profile(self):self.store_profile(ProfileDialog(self),str(uuid4()))
    def edit_profile(self):
        row=self.profile_table.currentRow()
        if row<0:return
        p=self.profiles[row]
        try:self.store_profile(ProfileDialog(self,IdentityVault().get(p['id']),p['alias']),p['id'])
        except FlowError as exc:QMessageBox.information(self,'Keychain',str(exc))
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
        except FlowError as exc:QMessageBox.information(self,'Keychain',str(exc))
    def export_config(self):
        self.save();path,_=QFileDialog.getSaveFileName(self,'导出非敏感配置','Apple配置.json','JSON (*.json)')
        if path:Path(path).write_text(json.dumps(self.config.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
    def import_config(self):
        path,_=QFileDialog.getOpenFileName(self,'载入配置','','JSON (*.json)')
        if path:
            try:
                self.config=Settings.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))
                # Loading a file never grants transaction/identity authority.
                self.config.auto_submit=False;self.config.transmit_identity=False
                self.config.privacy_policy_accepted=False;self.config.privacy_transfer_acknowledged=False;self.config.privacy_notice_version=''
                self.config.credential_id='';self.config.auto_login=False;self.config.trust_browser=False
                self.populate();self.repo.save_config(self.config)
            except Exception:QMessageBox.information(self,'配置未加载','配置格式不正确；请检查文件内容')
    def export_logs(self):
        path,_=QFileDialog.getSaveFileName(self,'导出脱敏日志','Apple运行日志.json','JSON (*.json)')
        if path:self.repo.export_logs(Path(path))
    def refresh_logs(self):
        rows=self.repo.events();self.log_table.setRowCount(len(rows))
        for row,e in enumerate(rows):
            for col,value in enumerate([self.local_timestamp(e['time']),STATE_LABELS.get(e['stage'],e['stage']),e['result'],str(round(e['elapsed'],1))]):self.log_table.setItem(row,col,QTableWidgetItem(value))
        self.log_table.setColumnWidth(2,400)
        changes=self.repo.stock_changes();self.stock_table.setRowCount(len(changes))
        for row,s in enumerate(changes):
            state='可选' if s['available'] else '不可选';before=s['previous_available']
            change='首次检测：'+state if before is None else ('可选' if before else '不可选')+' → '+state if before!=s['available'] else '取货说明变化'
            options=' · '.join(json.loads(s['options']).values())
            for col,value in enumerate([self.local_timestamp(s['time']),s['product']+' '+options,s['store_name'],change,s['quote']]):self.stock_table.setItem(row,col,QTableWidgetItem(value))
        orders=self.repo.submissions();self.orders_table.setRowCount(len(orders))
        labels={'CONFIRMED':'已创建 · 付款待官网核对','UNKNOWN':'结果待确认 · 禁止重下','PENDING':'提交中 / 待核对','REJECTED':'曾被拒绝 · 需新计划'}
        for row,o in enumerate(orders):
            handoff=self.payment_store.for_order(o['order_id']) if o.get('order_id') else None
            qr='已过记录时限' if handoff and handoff.expired() else '已保存 · 请核对有效性' if handoff else '未保存'
            for col,value in enumerate([o['order_id'][-4:] if o.get('order_id') else '尚未确认',labels.get(o['status'],o['status']),qr,self.local_timestamp(o['updated'])]):
                item=QTableWidgetItem(value);item.setData(Qt.ItemDataRole.UserRole,o.get('order_id'));self.orders_table.setItem(row,col,item)

    def local_timestamp(self,value):
        return datetime.fromisoformat(value).astimezone(ZoneInfo(self.current_zone())).strftime('%m-%d %H:%M:%S')

    def show_selected_payment(self):
        row=self.orders_table.currentRow()
        if row<0:
            self.message_label.setText('请先选中一个订单。');return
        order_id=self.orders_table.item(row,0).data(Qt.ItemDataRole.UserRole)
        handoff=self.payment_store.for_order(order_id) if order_id else None
        if not handoff:
            QMessageBox.information(self,'订单付款码','这个订单尚无已保存付款码。请在官网确认订单，当前浏览器显示二维码后可提取。');return
        self.show_payment(handoff=handoff)
    @Slot()
    def recover(self):
        if self.worker and self.worker.isRunning():self.resume()
        else:self.launch('website')
    @Slot(str)
    def launch(self,action):
        running=self.worker and self.worker.isRunning()
        if running and not self.worker.idle:
            if action=='login_check' and getattr(self.worker,'action',None) in ('login','login_diagnosis') and self.last_state=='PAUSED' and not self.worker.control.cancel_event.is_set() and not self.worker.control.stop_event.is_set():
                if time.monotonic()<self.cooldown_until:
                    self.message_label.setText(f'官网访问限制仍需等待 {math.ceil(self.cooldown_until-time.monotonic())} 秒，再检查；现有会话保留。')
                    return
                self.resume()
                self.message_label.setText('正在核验当前浏览器的登录结果…')
                return
            self.message_label.setText('已有浏览器任务在运行，请先停止或继续当前任务。');return
        if action not in ('environment','receipt','local','install') and time.monotonic()<self.cooldown_until:
            self.message_label.setText(f'官网访问限制仍需等待 {math.ceil(self.cooldown_until-time.monotonic())} 秒，再检查；现有会话保留。');return
        try:
            if action in ('environment','install','login','login_check','login_diagnosis','catalog'):
                config=deepcopy(self.config);config.keep_awake=self.keep_awake.isChecked();config.browser_kind=self.browser_kind.currentData();config.checkout_strategy=self.checkout_strategy.currentData()
                self.config=config;self.repo.save_config(config)
            else:
                config=self.collect()
                if action in ('website','live'):
                    mode=Mode.WEBSITE_DRY_RUN if action=='website' else Mode.LIVE
                    items=self.preparation_for(config,mode)
                    if any(item.blocks for item in items):
                        self.message_label.setText('请先处理准备清单中的缺失项。');self.show_preparation(mode=mode,items=items);return
                    config.validate(mode)
                self.config=config;self.repo.save_config(config)
            if action=='stores' and not any(s.enabled for s in config.stores):
                self.message_label.setText('请先从内置目录添加并启用门店，再检查当前商品库存。');self.stack.setCurrentIndex(1);return
        except (ConfigError,ValueError) as exc:QMessageBox.information(self,'请检查配置',str(exc));return
        self.clear_consent()
        if running:
            if getattr(self.worker,'session_kind','embedded' if getattr(self.worker,'embedded',None) else 'chrome')!=config.browser_kind:
                self.message_label.setText('当前会话仍在使用原来的浏览器。请关闭会话，再按所选模式打开。');return
            if not self.worker.enqueue(action,config):
                self.message_label.setText('先点“关闭会话”，再安装浏览器或运行本地异常测试。');return
        else:
            try:
                embedded=self.ensure_embedded() if config.browser_kind=='embedded' and action not in ('environment','install','local') else None
            except (FlowError,ConfigError) as error:self.message_label.setText(str(error));return
            self.worker=Worker(action,config,self.root,parent=self,embedded=embedded);self.worker.event.connect(self.on_event);self.worker.data.connect(self.on_data);self.worker.completed.connect(self.on_completed);self.worker.finished.connect(self.on_finished)
            self.worker.start()
        self.stop_button.setEnabled(True);self.close_session_button.setEnabled(True);self.resume_button.setEnabled(False);self.start_website.setEnabled(False);self.start_live.setEnabled(False)
        self.browser_kind.setEnabled(False)
        self.last_state='PREPARING'
        labels={'website':'官网试跑 · 到核对页停止','live':'真实购买','environment':'环境检查','install':'浏览器安装',
                'login':'预登录','login_check':'登录复核','login_diagnosis':'登录问题诊断','catalog':'商品目录','options':'商品选项',
                'stores':'门店查询','receipt':'订单核对','local':'本地异常测试 · 合成数据'}
        self.banner.setText(labels.get(action,'Apple 官网 · 软件浏览器'))
        self.stack.setCurrentIndex(6 if config.browser_kind=='embedded' and action in ('login','login_diagnosis') else 4);self.message_label.setText('正在启动任务…')
    @Slot(dict)
    def on_event(self,event):
        state=event.get('state','PREPARING');message=self.redactor.clean(event.get('message',''));self.message_label.setText(message);self.metrics['stage'].setText(STATE_LABELS.get(state,state))
        self.last_state=state
        if state!='PAUSED':self.clear_consent()
        self.console_log.appendPlainText(f'{datetime.now(ZoneInfo(self.current_zone())).strftime("%H:%M:%S")}  [{state}]  {message}')
        self.state_dot.setStyleSheet('color: '+('#b7791f' if state in ('PAUSED','FAILED','ORDER_UNCERTAIN') else '#8c8c94')+';')
        self.update_resume();self.run_events.append(event)
        stages=list(State)
        if state in stages:self.progressbar.setValue(min(12,stages.index(state)))
        if state in ('ORDER_CONFIRMED','PAYMENT_PENDING','ORDER_UNCERTAIN','PAUSED'):
            self.tray.showMessage('Apple AutoBuyer',message,QSystemTrayIcon.MessageIcon.Information,10000)
    @Slot(str,object)
    def on_data(self,kind,data):
        if kind=='catalog':
            self.catalog=data;self.catalog_cache.save(data);self.product_choices.replace_offers(data);self.catalog_selected(self.name.currentIndex())
        elif kind=='options':
            groups=groups_from_controls(data)
            if groups:
                offer=deepcopy(self.product_choices.offer);offer['groups']=groups
                self.product_choices.show_offer(offer,self.product_choices.selected_options())
        elif kind=='stores':
            stamp=datetime.now(ZoneInfo(self.current_zone())).strftime('%m-%d %H:%M:%S')
            self.store_results=[dict(s,observed_at=stamp) for s in data];self.render_store_directory()
        elif kind=='stock_changes':self.refresh_logs()
        elif kind=='stock_query':self.console_log.appendPlainText(f'官网供货查询完成：{data["store_count"]} 家门店，耗时 {data["elapsed"]:.2f} 秒；结果尚未锁定库存')
        elif kind=='bag':self.console_log.appendPlainText('购物袋已核对：'+json.dumps(data,ensure_ascii=False))
        elif kind=='customer_fields':self.console_log.appendPlainText('官网取货表单：'+('仅要求证件后四位；无类型下拉框' if data.get('only_last_four') and not data.get('document_types') else '按当前字段适配'))
        elif kind=='payments':self.console_log.appendPlainText('官网当前可读取付款选项：'+', '.join(data))
        elif kind=='installments':self.console_log.appendPlainText('官网当前分期方案：'+'; '.join(data))
        elif kind=='review':self.console_log.appendPlainText('官网最终核对：'+json.dumps(data,ensure_ascii=False))
        elif kind=='simulation':self.console_log.appendPlainText(f'本地测试提交次数：{data["submit_count"]}；合成订单：{data["synthetic_orders"]}')
        elif kind=='http_status':
            self.console_log.appendPlainText(f'官网 HTTP {data["status"]} · '+('认证服务请求失败，保留登录页，不自动重发密码' if data.get('affects')=='login_service' else '暂时拥堵 / 维护，按官网等待时间继续检查' if data.get('affects')=='store_busy' else '配送预估未能读取；自提结果另行核验' if data.get('affects')=='delivery_preview' else data['resource_type']+' · '+('暂停购买流程' if data['blocks_checkout'] else '非关键资源异常')))
            if data['blocks_checkout']:
                self.cooldown_until=max(self.cooldown_until,time.monotonic()+data.get('wait_seconds',60));self.update_resume()
                if data.get('context') in ('login','account'):
                    self.on_event({'state':'PAUSED','message':f'官网登录请求返回 HTTP {data["status"]}，暂时无法确认登录；至少等待 {data.get("wait_seconds",60)} 秒，现有浏览器会话保留。'})
        elif kind=='browser':
            self.browser_label.setText(data['name'])
            self.browser_session_note.setText('当前运行与诊断：'+data['name'])
            if data.get('kind') in ('embedded','chrome','daily_chrome'):
                self.browser_kind.setCurrentIndex(self.browser_kind.findData(data['kind']))
                self.show_active_browser(data['kind'])
        elif kind=='price_confirmation':self.show_price_choice(data)
        elif kind=='price_confirmed':self.console_log.appendPlainText(f'已接受本次官网价格：RMB {float(data["amount"]):,.2f}')
        elif kind=='consent_required':self.show_consent_help(data)
        elif kind=='consent_completed':
            self.clear_consent();self.console_log.appendPlainText('已按提前保存的选择完成 Apple 隐私确认；继续检查官网结账' if data.get('source')=='saved' else '已按本次选择完成 Apple 隐私确认；继续检查官网结账')
        elif kind=='browser_focus_error':self.message_label.setText(data)
        elif kind=='embedded_focus':self.showNormal();self.stack.setCurrentIndex(6);self.activateWindow()
        elif kind=='auth_focus':
            if self.worker and self.worker.embedded:self.stack.setCurrentIndex(6)
        elif kind=='auto_login':self.console_log.appendPlainText(data['message'])
        elif kind=='network_diagnosis':
            if data.get('network_error')=='net::ERR_ABORTED':return
            details=f'HTTP {data["status"]}' if 'status' in data else data.get('network_error','请求失败')
            if data.get('body_kind')=='html':details+=' · 返回 HTML'
            if data.get('error_class')=='apple_not_found_html':details+='（Apple 错误页）'
            if data.get('service_error_codes'):details+=' · Apple 错误码 '+', '.join(data['service_error_codes'])
            self.console_log.appendPlainText('登录诊断 · '+FAMILY_LABELS.get(data['family'],'官网请求')+' · '+data['host']+' · '+data.get('method','')+' → '+details)
        elif kind=='network_state':
            self.network_note.setText(network_summary(data))
            if data.get('changed'):
                self.console_log.appendPlainText('检测到本机网络设置变化；登录页与现有会话保留。请在同一官网登录页验证连接结果。')
        elif kind=='checkout_auth':self.console_log.appendPlainText(data['message'])
        elif kind=='verification_required':
            self.message_label.setText(data['message']);self.tray.showMessage('需要 Apple 账户验证' if data.get('kind')=='apple_account' else '需要手机验证',data['message'],QSystemTrayIcon.MessageIcon.Information,10000)
        elif kind=='product_link':
            self.console_log.appendPlainText('已从官网目录找到 '+data['name']+' 的购买入口')
            if normalize_name(self.name.currentText())==normalize_name(data['name']):self.url.setText(data['url'])
        elif kind=='login':
            self.login_observation=dict(data);self.login_label.setText(('登录已确认 · 北京时间 '+self.local_timestamp(data['checked_at'])) if data.get('authenticated') else ('登录状态：'+data.get('reason','暂时无法复核')))
        elif kind=='preorder_preparation':self.console_log.appendPlainText('官网提前准备入口：'+('、'.join(data['visible_entries']) if data['visible_entries'] else '当前商品页未显示；按开售时间等待购买流程开放'))
        elif kind=='session':
            self.browser_kind.setEnabled(True)
            self.session_open=True;self.start_website.setEnabled(True);self.start_live.setEnabled(True);self.resume_button.setEnabled(False);self.stop_button.setEnabled(False);self.close_session_button.setEnabled(True)
            self.banner.setText('官网浏览器会话保持打开');self.console_log.appendPlainText(data['message'])
        elif kind=='payment_qr':
            self.message_label.setText(f'订单尾号 {data["order_id"][-4:]} 的官网付款码已提取；付款结果以官网为准')
            self.tray.showMessage('Apple 订单待付款','付款二维码已提取，可保存或复制后发给下单人',QSystemTrayIcon.MessageIcon.Information,10000)
            self.refresh_logs();self.show_payment()
        elif kind=='payment_error':self.message_label.setText(data)
    def show_payment(self,checked=False,*,handoff=None):
        try:
            handoff=handoff or self.payment_store.latest()
            if not handoff:
                self.message_label.setText('尚无付款码。官网显示待付款订单后会自动提取，也可点“提取当前付款码”。');return
            if self.payment_dialog:self.payment_dialog.close()
            self.payment_dialog=PaymentDialog(self.payment_store,handoff,self);self.payment_dialog.show()
        except FlowError as exc:QMessageBox.information(self,'付款二维码',str(exc))
    def show_price_choice(self,data):
        if self.price_dialog and self.price_dialog.isVisible() and self.price_dialog.property('quote_key')==data['key']:return
        if self.price_dialog:self.price_dialog.close()
        worker=self.worker
        dialog=QDialog(self);dialog.setWindowTitle('确认官网价格');dialog.resize(430,230);dialog.setProperty('quote_key',data['key'])
        l=QVBoxLayout(dialog);self.label(l,data['product'],'sectionTitle')
        self.label(l,' · '.join(data.get('options',[])+[str(data['quantity'])+' 件']),'muted')
        self.label(l,f'RMB {float(data["amount"]):,.2f}','title');self.label(l,'按这个官网价格继续本次购买准备。实际付款由你完成。','muted')
        row=QHBoxLayout();self.button(row,'保持暂停',dialog.reject)
        def accept():
            if self.worker is worker and worker and worker.isRunning() and not worker.idle:
                worker.control.approve_quote(data['key']);worker.control.resume()
            dialog.accept()
        self.button(row,'接受这个价格并继续',accept,'primary');l.addLayout(row);self.price_dialog=dialog;dialog.show()

    def clear_consent(self):
        self.pending_consent=None
        if self.consent_dialog:self.consent_dialog.close()
        self.update_consent_entry()

    def update_consent_entry(self):
        active=bool(self.pending_consent and self.worker and self.worker.isRunning() and not self.worker.idle
                    and self.last_state=='PAUSED' and not self.worker.control.cancel_event.is_set()
                    and not self.worker.control.stop_event.is_set())
        self.consent_entry.setEnabled(active);self.console_consent_entry.setVisible(active)
        saved=self.privacy_policy.isChecked() and self.privacy_transfer.isChecked()
        self.consent_status.setText('当前官网声明需重新确认，请查看内容。' if active else '两项已勾选；保存后自动处理相同声明。' if saved else '勾选两项并保存，即可提前完成这份声明的同意。')

    def open_consent(self):
        self.update_consent_entry()
        if self.consent_entry.isEnabled():self.show_consent_help(self.pending_consent)

    def show_consent_help(self,data=None):
        data=data or {};key=data.get('key','')
        self.pending_consent=dict(data);self.update_consent_entry()
        if self.consent_dialog and self.consent_dialog.isVisible() and self.consent_dialog.property('consent_key')==key:return
        if self.consent_dialog:self.consent_dialog.close()
        worker=self.worker
        dialog=QDialog(self);dialog.setWindowTitle('官网隐私确认');dialog.resize(490,280);dialog.setProperty('consent_key',key)
        l=QVBoxLayout(dialog);self.label(l,'Apple 数据隐私知情同意书','sectionTitle')
        if key and len(data.get('choices',[]))==2:
            self.label(l,data['scope'],'muted');self.label(l,data['details'],'muted')
            self.button(l,'在官网查看协议',self.focus_browser,'link')
            choices=[]
            for label in data['choices']:
                check=QCheckBox('\n'.join(textwrap.wrap(label,width=30)));check.setAccessibleName(label);check.setMinimumHeight(48)
                check.setStyleSheet('QCheckBox { spacing: 8px; }');l.addWidget(check);choices.append(check)
            self.label(l,'点击确认仅接受当前页面的这份协议，本次任务使用一次。','muted')
            def accept_current():
                if self.worker is worker and worker and worker.isRunning() and not worker.idle and self.last_state=='PAUSED' and time.monotonic()>=self.cooldown_until and all(c.isChecked() for c in choices):
                    worker.control.approve_consent(key);worker.control.resume();dialog.accept()
            accept=self.button(l,'本次同意并继续',accept_current,'primary');accept.setEnabled(False)
            for check in choices:check.toggled.connect(lambda _,button=accept,checks=choices:button.setEnabled(all(c.isChecked() for c in checks)))
        else:
            self.label(l,'请在软件浏览器查看官网协议，并在官网作出选择。完成后点下面的按钮，软件会重新检查页面。','muted')
        row=QHBoxLayout();self.button(row,'暂不继续',dialog.reject)
        def completed():
            self.resume();dialog.accept()
        self.button(row,'已在官网处理，继续检查',completed,'primary');l.addLayout(row);self.consent_dialog=dialog;dialog.show()

    def focus_browser(self):
        if self.worker and self.worker.isRunning() and self.worker.session_open:
            self.worker.focus_requested.set()
        else:self.message_label.setText('请先点击“登录并保持会话”打开软件浏览器。')

    def request_payment(self):
        if self.worker and self.worker.isRunning():
            self.worker.qr_requested.set();self.message_label.setText('正在读取软件浏览器里的官网付款码…')
        else:self.message_label.setText('请先通过“登录并保持会话”打开软件自己的官网浏览器。已有二维码可点“查看付款二维码”。')
    @Slot(object)
    def on_completed(self,result):
        if isinstance(result,dict):
            if 'browser_installed' in result:self.browser_installed=result['browser_installed']
            self.on_event(result)
        self.refresh_logs()
        self.refresh_preparation()
    @Slot()
    def on_finished(self):
        self.clear_consent()
        self.browser_kind.setEnabled(True)
        self.browser_session_note.setText('浏览器会话已关闭；下一次诊断使用所选浏览器。')
        self.session_open=False;self.login_observation=None;self.close_session_button.setEnabled(False)
        self.login_label.setText('浏览器会话已关闭；重新打开后检查登录')
        for dialog in (self.price_dialog,self.consent_dialog):
            if dialog:dialog.close()
        self.stop_button.setEnabled(False);self.resume_button.setEnabled(False);self.start_website.setEnabled(True);self.start_live.setEnabled(True);self.refresh_preparation()
        if self.closing:self.close()
        elif self.pending_browser_action:
            action=self.pending_browser_action;self.pending_browser_action=None;single_shot(self,0,lambda:self.launch(action))
    @Slot()
    def resume(self):
        if self.worker and self.worker.isRunning() and not self.worker.idle and self.last_state=='PAUSED' and time.monotonic()>=self.cooldown_until and not self.worker.control.cancel_event.is_set():
            self.worker.control.resume();self.resume_button.setEnabled(False)
    @Slot()
    def stop(self):
        if self.worker and self.worker.isRunning() and not self.worker.idle:
            self.clear_consent()
            self.worker.stop_task();self.stop_button.setEnabled(False);self.resume_button.setEnabled(False);self.message_label.setText('正在停止当前任务并保留官网会话；提交结果不明时保留订单锁…')
    @Slot()
    def close_session(self):
        if self.worker and self.worker.isRunning():
            self.clear_consent()
            self.worker.control.stop();self.message_label.setText('正在关闭浏览器会话；配置和订单锁保留…')

    @staticmethod
    def editor_datetime(value):
        return QDateTime(QDate(value.year,value.month,value.day),QTime(value.hour,value.minute,value.second),QTimeZone.utc())

    def preparation_for(self,config,mode=Mode.LIVE):
        items=preparation_items(config,mode=mode,profile_ids=[p['id'] for p in self.repo.profiles()],browser_installed=self.browser_installed,
            session_open=bool(self.worker and self.worker.isRunning() and self.worker.session_open),login=self.login_observation,submission=self.repo.submission(config.intent_id))
        missing=self.product_choices.missing_choices()
        if missing:items[0]=PreparationItem('product','商品与配置','missing','请选择：'+'、'.join(missing),0,'设置商品')
        return items

    def refresh_preparation(self):
        try:
            items=self.preparation_for(self.collect());missing=sum(i.blocks for i in items);unknown=sum(i.status=='unknown' for i in items)
            self.preparation_summary.setText(f'准备检查：{missing} 项需处理 · {unknown} 项待检查 · 最终下单仍须官网核对' if missing else f'本机配置已齐 · {unknown} 项待检查 · 最终下单仍须官网核对')
            self.preparation_button.setToolTip(self.preparation_summary.text());self.refresh_plan_summary()
        except (ConfigError,ValueError) as exc:self.preparation_summary.setText('准备检查：'+str(exc))
        self.preparation_refresh_at=time.monotonic()+5

    def show_preparation(self,checked=False,*,mode=Mode.LIVE,items=None):
        try:
            items=items if items is not None else self.preparation_for(self.collect(),mode)
            if self.preparation_dialog:self.preparation_dialog.close()
            self.preparation_dialog=PreparationDialog(items,self.navigate_preparation,self);self.preparation_dialog.show()
        except (ConfigError,ValueError) as exc:
            self.stack.setCurrentIndex(4);self.message_label.setText(str(exc))

    def navigate_preparation(self,item:PreparationItem):
        self.stack.setCurrentIndex(item.page)
        actions={'检查环境':'environment','安装浏览器':'install','登录并保持会话':'login','检查登录状态':'login_check'}
        if item.action in actions:self.launch(actions[item.action])
        elif item.key=='schedule':self.schedule.setFocus()
        elif item.key=='product':self.name.setFocus()

    def update_resume(self):
        left=max(0,math.ceil(self.cooldown_until-time.monotonic()))
        running=bool(self.worker and self.worker.isRunning() and not self.worker.idle)
        self.resume_button.setText(f'等待 {left} 秒' if left and self.last_state=='PAUSED' else '继续检查')
        self.resume_button.setEnabled(running and self.last_state=='PAUSED' and not left and not self.worker.control.cancel_event.is_set())
        self.update_consent_entry()
    def tick(self):
        zone=ZoneInfo(self.current_zone());now=datetime.now(zone);self.metrics['clock'].setText(now.strftime('%H:%M:%S'));self.clock_caption.setText('北京时间')
        if self.schedule_enabled.isChecked():
            try:
                target=wall_time(self.schedule.dateTime().toPython(),self.current_zone());seconds=target.timestamp()-now.timestamp();left=max(0,int(seconds));self.metrics['countdown'].setText(f'{left//3600:02d}:{left%3600//60:02d}:{left%60:02d}' if seconds>0 else '预约时间已过');self.schedule_note.setText(schedule_preview(self.schedule.dateTime().toPython(),self.current_zone()))
            except ConfigError as exc:self.metrics['countdown'].setText('请检查时间');self.schedule_note.setText(str(exc))
        else:self.metrics['countdown'].setText('立即检查')
        if not self.schedule_enabled.isChecked():self.schedule_note.setText('未预约时立即准备；官网试跑立即执行。')
        self.update_resume()
        if time.monotonic()>=self.preparation_refresh_at:self.refresh_preparation()
    def closeEvent(self,event):
        if not self.closing and self.worker and self.worker.isRunning() and (self.worker.session_open or getattr(self.worker,'action','') in ('login','login_check','login_diagnosis','catalog','stores','options','website','live','receipt')):
            self.hide();event.ignore()
            self.tray.showMessage('AutoBuyer 继续运行','登录会话和任务保留。点击菜单栏图标恢复窗口；退出软件才会结束运行。',QSystemTrayIcon.MessageIcon.Information,5000)
        elif self.worker and self.worker.isRunning():self.closing=True;self.close_session();event.ignore()
        else:self.tray.hide();event.accept()

    @Slot()
    def shutdown(self):
        """Stop background work before Qt or Python destroys its parent widgets."""
        self.closing=True
        self.pending_browser_action=None
        self.timer.stop()
        self.tray.hide()
        if self.worker and self.worker.isRunning():
            worker=self.worker
            worker.control.stop()
            # WebEngine tab cleanup crosses back to Qt's main thread. Keep
            # those queued callbacks moving while the worker flushes/closes.
            while not worker.wait(25):
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        if self.embedded_browser:self.embedded_browser.shutdown()
