from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models.config import ConfigError


def wall_time(value: datetime, zone_name: str) -> datetime:
    """Interpret the date editor's wall clock without guessing a DST offset."""
    naive = value.replace(tzinfo=None)
    zone = ZoneInfo(zone_name)
    candidates = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == naive:
            candidates[candidate.timestamp()] = candidate
    if not candidates:
        raise ConfigError('这个当地时间因夏令时切换不存在，请选择其他时间或使用北京时间 / UTC')
    if len(candidates) > 1:
        raise ConfigError('这个当地时间在夏令时切换时出现两次，请用北京时间 / UTC 设置准确时刻')
    return next(iter(candidates.values()))


def convert_wall_time(value: datetime, old_zone: str, new_zone: str) -> datetime:
    return wall_time(value, old_zone).astimezone(ZoneInfo(new_zone)).replace(tzinfo=None)


def future_start(value: str, *, now: datetime | None = None) -> datetime:
    try:
        target = datetime.fromisoformat(value)
        if target.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError):
        raise ConfigError('开售时间须带时区') from None
    if target.astimezone(timezone.utc) <= (now or datetime.now(timezone.utc)).astimezone(timezone.utc):
        raise ConfigError('预约开售时间已过，请更新未来时间，或取消预约以立即开始')
    return target


def schedule_preview(value: datetime, zone_name: str) -> str:
    target = wall_time(value, zone_name)
    china = target.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    berlin = target.astimezone(ZoneInfo('Europe/Berlin')).strftime('%Y-%m-%d %H:%M:%S')
    return f'北京时间 {china}  /  柏林时间 {berlin}'
