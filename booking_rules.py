"""Pure booking rules shared by persistence and handlers.

This module contains no database access. Keeping validation and time-range logic
here makes storage.py responsible for persistence rather than business rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import config
from tz_utils import get_now

_working_time_cache: dict[str, list[str]] = {}


def normalize_duration_minutes(duration_minutes: int | None) -> int:
    try:
        duration = int(duration_minutes or config.DEFAULT_SERVICE_DURATION_MINUTES)
    except (TypeError, ValueError):
        duration = config.DEFAULT_SERVICE_DURATION_MINUTES
    return duration if duration > 0 else config.DEFAULT_SERVICE_DURATION_MINUTES


def normalize_master_key(master_key: str | None = None) -> str:
    value = (master_key or getattr(config, "MASTER_KEY", "default") or "default").strip()
    return value or "default"


def explicit_master_key(master: str | None) -> str:
    """Use an explicit resource key when supplied; preserve legacy calls."""
    if master and "|" in master:
        return normalize_master_key(master)
    return normalize_master_key()


def time_to_minutes(time_str: str) -> int:
    hours, minutes = time_str.split(":", 1)
    return int(hours) * 60 + int(minutes)


def minutes_to_time(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def slot_times_for_range(start_time: str, duration_minutes: int | None) -> list[str]:
    duration = normalize_duration_minutes(duration_minutes)
    start = time_to_minutes(start_time)
    count = (duration + config.SLOT_STEP_MINUTES - 1) // config.SLOT_STEP_MINUTES
    return [minutes_to_time(start + i * config.SLOT_STEP_MINUTES) for i in range(count)]


def working_time_slots_for_date(date_str: str) -> list[str]:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return []
    day_key = d.strftime("%A").lower()
    cached = _working_time_cache.get(day_key)
    if cached is not None:
        return cached
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    hours = config.WORKING_HOURS.get(day_names[d.weekday()])
    if not hours or len(hours) != 2:
        return []
    start_h, end_h = int(hours[0]), int(hours[1])
    if start_h >= end_h:
        return []
    slots = [f"{h:02d}:{minute:02d}" for h in range(start_h, end_h) for minute in (0, 30)]
    _working_time_cache[day_key] = slots
    return slots


def booking_range_fits_working_day(date_str: str, start_time: str, duration_minutes: int | None) -> bool:
    available_slots = set(working_time_slots_for_date(date_str))
    required_slots = slot_times_for_range(start_time, duration_minutes)
    return bool(required_slots) and all(slot in available_slots for slot in required_slots)


def booking_time_is_far_enough(date_str: str, start_time: str, now_provider=get_now) -> bool:
    try:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        h, m = [int(part) for part in start_time.split(":", 1)]
    except Exception:
        return False
    now = now_provider(config.TIMEZONE)
    if selected_date < now.date():
        return False
    if selected_date > now.date():
        return True
    slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return slot_dt > now + timedelta(minutes=config.MIN_BOOKING_ADVANCE_MINUTES)


def time_ranges_overlap(start_a: str, duration_a: int | None, start_b: str, duration_b: int | None) -> bool:
    a1 = time_to_minutes(start_a)
    a2 = a1 + normalize_duration_minutes(duration_a)
    b1 = time_to_minutes(start_b)
    b2 = b1 + normalize_duration_minutes(duration_b)
    return a1 < b2 and b1 < a2


def duration_between(start_time: str, end_time: str) -> int:
    return max(config.SLOT_STEP_MINUTES, time_to_minutes(end_time) - time_to_minutes(start_time))


def period_overlaps(start_time: str, duration_minutes: int | None, period: dict) -> bool:
    return time_ranges_overlap(
        start_time,
        duration_minutes,
        period["start_time"],
        duration_between(period["start_time"], period["end_time"]),
    )
