"""Global service catalog repository."""
from __future__ import annotations

import time

import config
import db
from booking_rules import normalize_duration_minutes

_services_cache: dict | None = None
_services_cache_time = 0.0
_SERVICES_CACHE_TTL = 30


def invalidate_cache() -> None:
    global _services_cache, _services_cache_time
    _services_cache = None
    _services_cache_time = 0.0


async def save_service(name: str, price: int, duration_minutes: int | None = None):
    duration = normalize_duration_minutes(duration_minutes or config.get_service_duration(name))
    async with db.acquire() as conn:
        await conn.upsert("services", ["name"], {"name": name, "price": price, "duration_minutes": duration})
        await conn.commit()
    invalidate_cache()


async def remove_service(name: str):
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM services WHERE name=?", name)
        await conn.commit()
    invalidate_cache()


async def get_all_services() -> dict:
    global _services_cache, _services_cache_time
    now = time.time()
    if _services_cache is not None and (now - _services_cache_time) < _SERVICES_CACHE_TTL:
        return _services_cache
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT name, price FROM services")
    _services_cache = {r["name"]: r["price"] for r in rows}
    _services_cache_time = now
    return _services_cache


async def get_all_service_durations() -> dict:
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT name, duration_minutes FROM services")
    return {r["name"]: normalize_duration_minutes(r.get("duration_minutes")) for r in rows}
