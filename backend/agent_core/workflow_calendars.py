"""Validated, versioned work calendars for generic workflow deadlines."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import DomainError


_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_CONFIG_KEYS = {"working_weekdays", "daily_intervals", "holiday_dates", "extra_work_dates"}


def validate_calendar(timezone: str, config: dict) -> dict:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise DomainError("INVALID_CALENDAR", "工作日历时区不是有效的 IANA 时区")
    if not isinstance(config, dict) or set(config) != _CONFIG_KEYS:
        raise DomainError("INVALID_CALENDAR", "工作日历配置字段不完整")
    weekdays = config.get("working_weekdays")
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or any(type(item) is not int or item < 1 or item > 7 for item in weekdays)
        or len(weekdays) != len(set(weekdays))
    ):
        raise DomainError("INVALID_CALENDAR", "工作周必须包含不重复的星期编号 1 至 7")
    intervals = config.get("daily_intervals")
    if not isinstance(intervals, list) or not 1 <= len(intervals) <= 8:
        raise DomainError("INVALID_CALENDAR", "每天须配置 1 至 8 个工作时段")
    normalized_intervals = []
    previous_end = None
    for interval in intervals:
        if not isinstance(interval, dict) or set(interval) != {"start", "end"}:
            raise DomainError("INVALID_CALENDAR", "工作时段必须包含开始和结束时间")
        start, end = interval.get("start"), interval.get("end")
        if not isinstance(start, str) or not isinstance(end, str) or not _TIME.fullmatch(start) or not _TIME.fullmatch(end):
            raise DomainError("INVALID_CALENDAR", "工作时段须使用 HH:MM 格式")
        if start >= end or (previous_end is not None and start < previous_end):
            raise DomainError("INVALID_CALENDAR", "工作时段必须按时间排序且不能重叠")
        normalized_intervals.append({"start": start, "end": end})
        previous_end = end
    holidays = _validate_dates(config.get("holiday_dates"), "休息日")
    extra = _validate_dates(config.get("extra_work_dates"), "补班日")
    if set(holidays) & set(extra):
        raise DomainError("INVALID_CALENDAR", "同一天不能同时是休息日和补班日")
    return {
        "working_weekdays": sorted(weekdays),
        "daily_intervals": normalized_intervals,
        "holiday_dates": sorted(holidays),
        "extra_work_dates": sorted(extra),
    }


def _validate_dates(values, label):
    if not isinstance(values, list) or len(values) > 1000 or any(not isinstance(value, str) for value in values):
        raise DomainError("INVALID_CALENDAR", f"{label}必须是日期列表且最多 1000 项")
    if len(values) != len(set(values)):
        raise DomainError("INVALID_CALENDAR", f"{label}不能重复")
    for value in values:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError:
            raise DomainError("INVALID_CALENDAR", f"{label}须使用 YYYY-MM-DD 格式")
    return values


def add_working_hours(started_at: datetime, hours: int, timezone: str, config: dict) -> datetime:
    """Advance across configured local work intervals and return an aware instant."""
    normalized = validate_calendar(timezone, config)
    zone = ZoneInfo(timezone)
    cursor = started_at if started_at.tzinfo else started_at.replace(tzinfo=zone)
    cursor = cursor.astimezone(zone)
    remaining = timedelta(hours=hours)
    holidays = set(normalized["holiday_dates"])
    extra = set(normalized["extra_work_dates"])
    weekdays = set(normalized["working_weekdays"])
    parsed_intervals = [
        (time.fromisoformat(item["start"]), time.fromisoformat(item["end"]))
        for item in normalized["daily_intervals"]
    ]
    for _ in range(40000):
        day = cursor.date()
        day_key = day.isoformat()
        is_workday = day_key in extra or (day.isoweekday() in weekdays and day_key not in holidays)
        if is_workday:
            for start_time, end_time in parsed_intervals:
                interval_start = datetime.combine(day, start_time, zone)
                interval_end = datetime.combine(day, end_time, zone)
                active = max(cursor, interval_start)
                if active >= interval_end:
                    continue
                available = interval_end - active
                if remaining <= available:
                    return active + remaining
                remaining -= available
        cursor = datetime.combine(day + timedelta(days=1), time.min, zone)
    raise DomainError("INVALID_CALENDAR", "工作日历无法在合理范围内计算办理时限")
