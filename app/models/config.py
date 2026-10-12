from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import uuid4
import re
import math
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

class Mode(StrEnum):
    WEBSITE_DRY_RUN = "website_dry_run"
    LIVE = "live"
    LOCAL_TEST = "local_test"

class State(StrEnum):
    WAITING = "WAITING"
    PREPARING = "PREPARING"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    STORE_OPEN = "STORE_OPEN"
    PRODUCT_SELECTED = "PRODUCT_SELECTED"
    PICKUP_SELECTED = "PICKUP_SELECTED"
    CUSTOMER_INFO_FILLED = "CUSTOMER_INFO_FILLED"
    PAYMENT_SELECTED = "PAYMENT_SELECTED"
    REVIEW_READY = "REVIEW_READY"
    ORDER_SUBMITTING = "ORDER_SUBMITTING"
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    ORDER_UNCERTAIN = "ORDER_UNCERTAIN"
    FAILED = "FAILED"
    STOPPED = "STOPPED"

STATE_LABELS = dict(zip(State, [
    "等待开售", "提前准备", "商店暂未开放", "购买页可访问", "商品已配置", "门店已选择（未锁库存）",
    "取货信息已校验", "付款方式已选择", "等待最终核对", "正在提交", "服务器已确认订单", "等待用户付款",
    "已完成", "等待你操作", "订单结果待确认", "未通过", "已停止"
]))

class ConfigError(ValueError):
    pass

def money(value: str | int | float | Decimal) -> Decimal:
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ConfigError("金额格式不正确") from None
    if not result.is_finite() or result < 0:
        raise ConfigError("金额必须为有限的非负数字")
    try:
        return result.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ConfigError('金额超出可用范围') from None

def apple_url(url: str) -> bool:
    if not isinstance(url,str):
        return False
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and (host == "apple.com.cn" or host.endswith(".apple.com.cn")))
    except ValueError:
        return False

@dataclass
class Product:
    name: str = "iPhone Duo"
    url: str = "https://www.apple.com.cn/shop/buy-iphone/iphone-duo"
    # Keys are observed input names; values are observed choice labels/values. No SKU defaults.
    options: dict[str, str] = field(default_factory=dict)
    quantity: int = 1
    max_total: str = "0.00"
    region: str = "中国大陆"
    exclude_trade_in: bool = True
    exclude_applecare: bool = True
    price_from_website: bool = False
    discover_from_store: bool = False

@dataclass
class Store:
    name: str
    city: str = "上海"
    enabled: bool = True
    store_id: str = ""
    province: str = "上海"
    district: str = "黄浦区"

@dataclass
class Settings:
    product: Product = field(default_factory=Product)
    stores: list[Store] = field(default_factory=list)
    payments: list[str] = field(default_factory=lambda: ["支付宝", "微信支付"])
    installment_months: int = 0
    profile_id: str = ""
    auto_submit: bool = False
    transmit_identity: bool = False
    privacy_policy_accepted: bool = False
    privacy_transfer_acknowledged: bool = False
    privacy_notice_version: str = ''
    checkout_strategy: str = 'account_then_guest'
    credential_id: str = ''
    auto_login: bool = False
    trust_browser: bool = False
    browser_kind: str = 'embedded'
    keep_awake: bool = True
    allow_other_stores: bool = False
    no_stock: str = "wait"
    pickup_from: str = ""
    pickup_to: str = ""
    starts_at: str = ""
    display_timezone: str = "Asia/Shanghai"
    interval: float = 10.0
    max_interval: float = 60.0
    max_wait: float = 1800.0
    operation_timeout: float = 15.0
    reconcile_timeout: float = 45.0
    intent_id: str = field(default_factory=lambda: str(uuid4()))
    scenario: str = "first_available"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Settings:
        allowed = set(cls.__dataclass_fields__) - {"product", "stores"}
        if not isinstance(raw, dict) or not isinstance(raw.get("product", {}), dict):
            raise ConfigError("配置格式不正确")
        # Reject fields that might carry identities instead of silently persisting them.
        if set(raw) - set(cls.__dataclass_fields__):
            raise ConfigError("配置包含未知字段")
        product_raw = raw.get("product", {})
        if set(product_raw) - set(Product.__dataclass_fields__):
            raise ConfigError("商品配置包含未知字段")
        try:
            result = cls(product=Product(**product_raw), stores=[Store(**s) for s in raw.get("stores", [])],
                         **{k: v for k, v in raw.items() if k in allowed})
        except (TypeError, KeyError):
            raise ConfigError("配置格式不正确") from None
        # An upgrade must not silently abandon a user's existing Chrome login.
        if 'browser_kind' not in raw and raw:
            result.browser_kind = 'chrome'
        result.validate(Mode.WEBSITE_DRY_RUN, require_stores=False)
        return result

    def validate(self, mode: Mode, *, require_stores: bool = True):
        if not isinstance(self.product,Product) or not isinstance(self.stores,list) or any(not isinstance(s,Store) for s in self.stores):
            raise ConfigError('商品或门店配置格式不正确')
        if type(self.product.price_from_website) is not bool or type(self.product.discover_from_store) is not bool:
            raise ConfigError('商品价格和目录选择格式不正确')
        if mode != Mode.LOCAL_TEST and not (apple_url(self.product.url) or (self.product.discover_from_store and self.product.url=='')):
            raise ConfigError("仅支持 Apple 中国大陆官网 HTTPS 商品链接")
        if not isinstance(self.product.name, str) or not self.product.name.strip():
            raise ConfigError("请填写商品名称")
        if type(self.product.quantity) is not int or not 1 <= self.product.quantity <= 10:
            raise ConfigError("数量须为 1–10，仍须遵守官网实际限购")
        if not isinstance(self.product.options, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k,v in self.product.options.items()):
            raise ConfigError("商品选项须为文字键值")
        if mode != Mode.LOCAL_TEST and money(self.product.max_total) <= 0:
            # An unsaved blank configuration is valid for editing but never for running.
            if require_stores and not self.product.price_from_website:
                raise ConfigError("请设置最大订单总金额（包括全部商品及费用）")
        else:
            money(self.product.max_total)
        if require_stores and not any(s.enabled for s in self.stores):
            raise ConfigError("请先添加至少一家允许的自提门店")
        if not isinstance(self.payments,list) or not self.payments or any(not isinstance(p, str) or not p.strip() for p in self.payments):
            raise ConfigError("请设置允许的付款方式")
        if type(self.installment_months) is not int or not 0 <= self.installment_months <= 60:
            raise ConfigError('分期期数须为 0–60；0 表示不选分期，实际可用方案由官网验证')
        if self.no_stock not in ("wait", "stop"):
            raise ConfigError("缺货策略须为等待或停止")
        if self.checkout_strategy not in ('account_then_guest', 'account_only', 'guest'):
            raise ConfigError('请选择有效的结账登录策略')
        if self.browser_kind not in ('embedded', 'chrome', 'daily_chrome'):
            raise ConfigError('请选择内置浏览器、独立 Chrome 或日常 Chrome')
        for key in ("auto_submit", "transmit_identity", "privacy_policy_accepted", "privacy_transfer_acknowledged", "allow_other_stores", "keep_awake", "auto_login", "trust_browser"):
            if type(getattr(self, key)) is not bool:
                raise ConfigError("开关配置格式不正确")
        if not isinstance(self.credential_id, str) or (self.credential_id and not re.fullmatch(
                r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', self.credential_id)):
            raise ConfigError('Apple 账户的钥匙串引用格式不正确')
        if (self.auto_login or self.trust_browser) and not self.credential_id:
            raise ConfigError('请先在官网浏览器页保存 Apple 账户')
        if any(type(s.enabled) is not bool for s in self.stores):
            raise ConfigError("门店开关格式不正确")
        if any(not all(isinstance(getattr(s,k),str) for k in ('name','city','store_id','province','district')) or not s.name.strip() for s in self.stores):
            raise ConfigError('门店文字配置格式不正确')
        if len({s.store_id or s.name for s in self.stores}) != len(self.stores):
            raise ConfigError("门店列表存在重复项")
        if not all(isinstance(getattr(self,k),str) for k in ('profile_id','intent_id','pickup_from','pickup_to','starts_at','display_timezone','scenario','privacy_notice_version')) or not self.intent_id:
            raise ConfigError('配置引用、日期或时区格式不正确')
        try:
            ZoneInfo(self.display_timezone)
        except (ZoneInfoNotFoundError,ValueError):
            raise ConfigError('显示时区不存在') from None
        if any(type(getattr(self,k)) not in (int,float) or not math.isfinite(getattr(self,k)) for k in ('interval','max_interval','max_wait','operation_timeout','reconcile_timeout')):
            raise ConfigError('时间间隔和超时须为有限数字')
        if not 0.05 <= self.interval <= self.max_interval <= 600 or not 0 < self.max_wait <= 86400:
            raise ConfigError("检查间隔或等待时长超出范围")
        if not 0 < self.operation_timeout <= 120 or not 0 < self.reconcile_timeout <= 300:
            raise ConfigError("操作超时配置超出范围")
        if self.starts_at:
            try:
                target = datetime.fromisoformat(self.starts_at)
                if target.tzinfo is None:
                    raise ValueError
            except (ValueError, TypeError):
                raise ConfigError("开售时间须带时区") from None
        try:
            lo = date.fromisoformat(self.pickup_from) if self.pickup_from else None
            hi = date.fromisoformat(self.pickup_to) if self.pickup_to else None
            if lo and hi and lo > hi:
                raise ValueError
        except ValueError:
            raise ConfigError("取货日期范围不正确") from None
        if mode == Mode.LIVE and (not self.profile_id or not self.transmit_identity):
            raise ConfigError("真实运行需要 Keychain 取货人及向 Apple 填写信息的授权")

@dataclass(repr=False)
class Identity:
    name: str
    document_type: str
    document_number: str
    phone: str
    email: str
    family_name: str = ""
    given_name: str = ""

    def __repr__(self):
        return "Identity(<private>)"

    def validate(self, *, test: bool = False):
        if not all(isinstance(getattr(self,k), str) and getattr(self,k).strip() for k in ('name','document_type','document_number','phone','email')):
            raise ConfigError("取货人信息不完整")
        if test:
            if not self.document_number.startswith("TEST-") or not self.email.endswith(".invalid"):
                raise ConfigError("本地测试只能使用合成身份")
            return
        if self.document_number.startswith('TEST-') or self.email.endswith('.invalid') or self.document_type=='测试证件':
            raise ConfigError('合成身份不可用于 Apple 官网')
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.email):
            raise ConfigError("邮箱格式不正确")
        if not re.fullmatch(r"\+?[0-9 \-]{7,18}", self.phone):
            raise ConfigError("手机号码格式不正确")
        if '后四位' in self.document_type:
            resident='居民身份证' in self.document_type
            pattern=r'[0-9]{3}[0-9Xx]' if resident else r'[A-Za-z0-9]{4}'
            if not re.fullmatch(pattern,self.document_number):
                raise ConfigError('请填写证件后四位：四位数字，身份证末位为 X 时也可填写 X' if resident else '请填写证件号码的后四位')
        elif "身份证" in self.document_type:
            number = self.document_number.upper()
            if not re.fullmatch(r"[1-9]\d{16}[\dX]", number):
                raise ConfigError("居民身份证格式不正确")
            try:
                datetime.strptime(number[6:14], "%Y%m%d")
            except ValueError:
                raise ConfigError("居民身份证日期不正确") from None
            weights = [7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2]
            if "10X98765432"[sum(int(n)*w for n,w in zip(number[:17],weights)) % 11] != number[-1]:
                raise ConfigError("居民身份证校验位不正确")
        elif "护照" in self.document_type and not re.fullmatch(r"[A-Za-z0-9]{5,20}",self.document_number):
            raise ConfigError("护照号码格式不正确")

    @classmethod
    def synthetic(cls):
        return cls("模拟取货人", "测试证件", "TEST-000001", "00000000000", "tester@example.invalid")
