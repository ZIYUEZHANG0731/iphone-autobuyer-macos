from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from app.models.flow import FlowError
from app.utils.files import write_private_json


@dataclass
class PaymentHandoff:
    order_id: str
    image_file: str
    captured_at: str
    payment: str = ""
    product: str = ""
    total_cny: str = ""
    installment_months: int = 0
    payment_window_minutes: int | None = None
    payment_deadline: str | None = None
    payment_app: str = ""

    def expired(self, now: datetime | None = None) -> bool:
        if not self.payment_deadline:
            return False
        return (now or datetime.now(timezone.utc)) >= datetime.fromisoformat(self.payment_deadline)


class PaymentStore:
    """Local payment images only. Never persist the QR payload or its source URL."""

    def __init__(self, root: Path):
        self.directory = root / "payment-qr"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)

    def save(self, image: bytes, *, order_id: str, payment="", product="", total_cny="",
             installment_months=0, payment_window_minutes=None, submitted_at: datetime | None = None,
             payment_app="") -> PaymentHandoff:
        if not re.fullmatch(r"[A-Z]\d{8,16}", order_id):
            raise FlowError("官网订单号无法可靠确认，付款码未保存")
        if len(image) > 8 * 1024 * 1024:
            raise FlowError("付款二维码图片大小异常")
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = ".png"
        elif image.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        else:
            raise FlowError("付款二维码图片格式不正确")
        if payment_window_minutes is not None and not 0 < payment_window_minutes <= 1440:
            raise FlowError("官网付款时限无法可靠确认")
        if submitted_at is not None and submitted_at.tzinfo is None:
            raise FlowError("付款起算时间必须包含时区")
        # Relative text is NOT a fresh countdown every time the page is inspected.
        deadline = (submitted_at + timedelta(minutes=payment_window_minutes)).isoformat() if submitted_at and payment_window_minutes else None
        image_file = uuid4().hex + extension
        handoff = PaymentHandoff(order_id, image_file, datetime.now(timezone.utc).isoformat(),
            payment, product, total_cny, installment_months, payment_window_minutes, deadline, payment_app)
        path = self.directory / image_file
        path.write_bytes(image)
        path.chmod(0o600)
        self._write_manifest(self.directory / (Path(image_file).stem + ".json"), handoff)
        self._write_manifest(self.directory / "latest.json", handoff)
        return handoff

    @staticmethod
    def _write_manifest(path: Path, handoff: PaymentHandoff):
        write_private_json(path, asdict(handoff))

    def image_path(self, handoff: PaymentHandoff) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}\.(?:png|jpg)", handoff.image_file):
            raise FlowError("付款码文件引用不正确")
        path = self.directory / handoff.image_file
        if path.is_symlink() or not path.is_file():
            raise FlowError("付款码图片不存在，请重新从官网提取")
        return path

    def latest(self) -> PaymentHandoff | None:
        path = self.directory / "latest.json"
        if not path.exists():
            return None
        try:
            handoff = PaymentHandoff(**json.loads(path.read_text(encoding="utf-8")))
            self.image_path(handoff)
            if not re.fullmatch(r"[A-Z]\d{8,16}", handoff.order_id):
                raise ValueError()
            if handoff.payment_deadline and datetime.fromisoformat(handoff.payment_deadline).tzinfo is None:
                raise ValueError()
            return handoff
        except (ValueError, TypeError, OSError, FlowError):
            raise FlowError("本机付款码记录无法读取，请重新从官网提取") from None

    def for_order(self, order_id: str) -> PaymentHandoff | None:
        if not re.fullmatch(r"[A-Z]\d{8,16}", order_id):
            return None
        matches = []
        for path in self.directory.glob("*.json"):
            if path.name == "latest.json":
                continue
            try:
                handoff = PaymentHandoff(**json.loads(path.read_text(encoding="utf-8")))
                if handoff.order_id == order_id:
                    self.image_path(handoff)
                    if handoff.payment_deadline and datetime.fromisoformat(handoff.payment_deadline).tzinfo is None:
                        raise ValueError()
                    matches.append(handoff)
            except (ValueError, TypeError, OSError, FlowError):
                continue
        return min(matches, key=lambda h:h.captured_at) if matches else None

    def set_latest(self, handoff: PaymentHandoff):
        self.image_path(handoff)
        self._write_manifest(self.directory / "latest.json", handoff)
