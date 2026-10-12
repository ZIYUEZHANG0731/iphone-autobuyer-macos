from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from app.models.config import State, Store, Settings, money

class FlowError(Exception):
    """Only fixed, public error messages; do not wrap Playwright messages with PII."""

class UserAction(FlowError):
    pass

class RateLimited(UserAction):
    def __init__(self, message, *, retry_after=0., retry_opening=False):
        super().__init__(message)
        self.retry_after = max(0., float(retry_after))
        self.retry_opening = retry_opening

class Unavailable(FlowError):
    pass

class SubmissionUnknown(FlowError):
    pass

class Stopped(FlowError):
    pass

@dataclass
class Availability:
    store: Store
    available: bool
    quote: str
    pickup_date: str | None = None

@dataclass
class Summary:
    product: str
    options: dict[str,str]
    quantity: int
    total: Decimal
    store_name: str
    store_id: str
    payment: str = ""
    fulfillment: str = "pickup"
    currency: str = "CNY"
    verified: bool = False
    installment_months: int = 0

    def authorize(self, config: Settings):
        if not self.verified:
            raise UserAction("订单摘要尚未可靠解析，需手动核对")
        if self.currency != "CNY" or not self.total.is_finite() or self.total <= 0 or self.total > money(config.product.max_total):
            raise FlowError("订单金额超出授权或无法可靠确认")
        if self.quantity != config.product.quantity:
            raise FlowError("订单数量与授权不一致")
        if self.product != config.product.name or self.options != config.product.options:
            raise FlowError("订单商品配置与授权不一致")
        if self.fulfillment != "pickup":
            raise FlowError("未授权邮寄配送")
        allowed = any(s.enabled and ((s.store_id and self.store_id == s.store_id) or (not s.store_id and self.store_name == s.name)) for s in config.stores)
        if not allowed and not config.allow_other_stores:
            raise FlowError("订单门店未获授权")
        if self.payment not in config.payments:
            raise FlowError("订单付款方式未获授权")
        if self.installment_months != config.installment_months:
            raise FlowError('订单分期期数与授权不一致')

@dataclass
class Receipt:
    order_id: str
    status: str
    payment_deadline: str | None = None

@dataclass
class RunResult:
    run_id: str
    mode: str
    state: str
    message: str
    steps: list[dict] = field(default_factory=list)
    order_id: str | None = None

# All interruption states preserve the previous stage; continue never jumps into submission.
_FORWARD = {
    State.WAITING: {State.PREPARING},
    State.PREPARING: {State.WAITING,State.STORE_UNAVAILABLE,State.STORE_OPEN,State.ORDER_CONFIRMED,State.ORDER_UNCERTAIN},
    State.STORE_UNAVAILABLE: {State.STORE_OPEN,State.STORE_UNAVAILABLE},
    State.STORE_OPEN: {State.PRODUCT_SELECTED},
    State.PRODUCT_SELECTED: {State.PICKUP_SELECTED,State.PRODUCT_SELECTED},
    State.PICKUP_SELECTED: {State.CUSTOMER_INFO_FILLED,State.REVIEW_READY},
    State.CUSTOMER_INFO_FILLED: {State.PAYMENT_SELECTED,State.REVIEW_READY},
    State.PAYMENT_SELECTED: {State.REVIEW_READY},
    State.REVIEW_READY: {State.ORDER_SUBMITTING,State.COMPLETED},
    State.ORDER_SUBMITTING: {State.ORDER_CONFIRMED,State.ORDER_UNCERTAIN},
    State.ORDER_UNCERTAIN: {State.ORDER_CONFIRMED},
    State.ORDER_CONFIRMED: {State.PAYMENT_PENDING,State.COMPLETED},
    State.PAYMENT_PENDING: {State.COMPLETED},
}

class StateMachine:
    def __init__(self):
        self.state = State.WAITING
        self.previous = State.WAITING

    def move(self, target: State):
        if target == self.state:
            return
        if target in (State.PAUSED,State.STOPPED,State.FAILED):
            self.previous = self.state
        elif self.state == State.PAUSED and target == self.previous:
            pass
        elif target not in _FORWARD.get(self.state, set()):
            raise FlowError("不允许的购买阶段跳转")
        self.state = target
