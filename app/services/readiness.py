from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Collection

from app.models.config import Settings, Mode, ConfigError, apple_url, money
from app.services.payment_methods import payment_plan
from app.utils.scheduling import future_start


@dataclass(frozen=True)
class PreparationItem:
    key: str
    title: str
    status: str
    detail: str
    page: int
    action: str

    @property
    def blocks(self) -> bool:
        return self.status == 'missing'


def preparation_items(config: Settings, *, mode: Mode = Mode.LIVE,
                      profile_ids: Collection[str] = (), browser_installed: bool | None = None,
                      session_open: bool = False, login: dict | None = None,
                      submission: dict | None = None, now: datetime | None = None) -> list[PreparationItem]:
    """Local preparation only; never read Keychain values or claim site availability."""
    now = now or datetime.now(timezone.utc)
    items = []

    def add(key, title, status, detail, page, action):
        items.append(PreparationItem(key, title, status, detail, page, action))

    try:
        product_ready = (bool(config.product.name.strip()) and (apple_url(config.product.url) or (config.product.discover_from_store and config.product.url==''))
                         and (config.product.price_from_website or money(config.product.max_total) > 0) and 1 <= config.product.quantity <= 10)
    except (ConfigError, TypeError):
        product_ready = False
    add('product', '商品与配置', 'ready' if product_ready else 'missing',
        f'{config.product.name} · {config.product.quantity} 件；官网价格和选择将在运行时核对'
        if product_ready else '选择机型、配置与数量；更新官网目录可读取新机型', 0, '设置商品')

    stores = [s.name for s in config.stores if s.enabled]
    add('stores', '自提门店', 'ready' if stores else 'missing',
        '按优先级：' + ' → '.join(stores) + '；当前未锁定库存' if stores else '添加并启用至少一家允许的自提门店', 1, '设置门店')

    profile = bool(config.profile_id and config.profile_id in profile_ids)
    identity_ready = profile and config.transmit_identity
    add('identity', '取货人和填写授权', 'ready' if identity_ready else 'manual' if mode == Mode.WEBSITE_DRY_RUN else 'missing',
        '已填写本次取货资料并允许向 Apple 填写，不读取钥匙串' if identity_ready
        else '官网试跑会暂停，允许在官网手动填写' if mode == Mode.WEBSITE_DRY_RUN
        else '在软件填写取货人或导入 TXT，再允许向 Apple 填写', 2 if not profile else 3,
        '设置取货人' if not profile else '设置填写授权')

    payments = bool(config.payments and all(p.strip() for p in config.payments))
    payment_detail = payment_plan(config.payments,config.installment_months)
    add('payment', '付款方案', 'ready' if payments else 'missing',
        payment_detail + '；是否可用以结账页为准' if payments else '至少选择一种允许的付款方式', 3, '设置付款')

    if mode == Mode.WEBSITE_DRY_RUN:
        add('schedule', '执行时间', 'ready', '官网试跑立即执行，不等待预约时间、不自动下单', 4, '查看时间')
    elif config.starts_at:
        try:
            target = future_start(config.starts_at, now=now)
            add('schedule', '执行时间', 'ready', target.isoformat(), 4, '设置时间')
        except ConfigError as exc:
            add('schedule', '执行时间', 'missing', str(exc), 4, '设置时间')
    else:
        add('schedule', '执行时间', 'ready', '立即开始购买准备；等待首发请勾选预约并设置开售时间', 4, '设置时间')

    add('browser', '浏览器环境', 'ready' if browser_installed else 'missing' if browser_installed is False else 'unknown',
        '官网浏览器已安装' if browser_installed else '对应浏览器未安装，请先安装' if browser_installed is False else '尚未检查浏览器是否已安装',
        4, '检查环境' if browser_installed is not False else '安装浏览器')

    verified = False
    if session_open and login and login.get('authenticated') is True:
        try:
            checked = datetime.fromisoformat(login['checked_at'])
            age = (now - checked).total_seconds()
            verified = checked.tzinfo is not None and 0 <= age <= 600
        except (KeyError, ValueError, TypeError):
            pass
    guest=config.checkout_strategy=='guest'
    fallback=config.checkout_strategy=='account_then_guest'
    add('login', '预登录会话', 'ready' if verified or guest else 'unknown',
        '选择了无需预登录的游客策略；实际仍可能要求手机验证码' if guest else
        '最近 10 分钟内确认登录，软件浏览器仍打开；官网仍可能要求认证' if verified
        else '已预选使用本次填写的账号密码；需提前完成首次验证，Apple 仍可能再次要求验证码' if config.auto_login
        else '登录暂未确认；结账出现登录页时按游客备用继续，手机验证仍可能暂停' if fallback
        else '现有会话需复核，请提前登录；失效时按设置等待重新登录', 4,
        '检查登录状态' if session_open else '登录并保持会话')

    if submission:
        suffix = (' · 订单尾号 ' + submission['order_id'][-4:]) if submission.get('order_id') else ''
        add('order', '购买计划', 'missing' if mode == Mode.LIVE else 'manual',
            '当前计划已有提交记录' + suffix + '，先核对旧订单；另一次购买须创建新计划', 5, '查看订单记录')
    else:
        add('order', '购买计划', 'ready', '当前计划没有订单提交记录；下单时仍会原子锁定以防重复', 3, '查看购买授权')

    add('handoff', '最终核对与下单', 'ready' if mode == Mode.LIVE and config.auto_submit else 'manual',
        '官网试跑到核对页自动结束，不创建订单' if mode == Mode.WEBSITE_DRY_RUN else
        '已允许自动提交：门店保存及最终商品、数量、金额、付款方案核对通过后下单；付款由你完成' if config.auto_submit else
        '自动提交未启用：软件完成购买准备后由你在官网核对并下单', 3, '查看购买授权')
    return items
