import pytz
from datetime import datetime
from typing import Optional

DEFAULT_TIMEZONE: str = "Asia/Almaty"


def get_now(timezone: str = DEFAULT_TIMEZONE) -> datetime:
    tz = pytz.timezone(timezone)
    return datetime.now(tz)


def format_datetime(dt: datetime, timezone: str = DEFAULT_TIMEZONE) -> str:
    tz = pytz.timezone(timezone)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def parse_datetime(date_str: str, time_str: str, timezone: str = DEFAULT_TIMEZONE) -> datetime:
    tz = pytz.timezone(timezone)
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    return tz.localize(dt)


def get_today(timezone: str = DEFAULT_TIMEZONE) -> str:
    return get_now(timezone).strftime("%Y-%m-%d")


def get_tomorrow(timezone: str = DEFAULT_TIMEZONE) -> str:
    from datetime import timedelta
    return (get_now(timezone) + timedelta(days=1)).strftime("%Y-%m-%d")


def get_current_time(timezone: str = DEFAULT_TIMEZONE) -> str:
    return get_now(timezone).strftime("%H:%M")


def is_past(date_str: str, time_str: str, timezone: str = DEFAULT_TIMEZONE) -> bool:
    dt = parse_datetime(date_str, time_str, timezone)
    return dt < get_now(timezone)
