"""Key-value settings repository with a short-lived read cache."""
from __future__ import annotations

import time

import db

_settings_cache: dict | None = None
_settings_cache_time = 0.0
_SETTINGS_CACHE_TTL = 30


def invalidate_cache() -> None:
    global _settings_cache, _settings_cache_time
    _settings_cache = None
    _settings_cache_time = 0.0


async def save_settings(key: str, value: str):
    async with db.acquire() as conn:
        await conn.upsert("settings", ["key"], {"key": key, "value": value})
        await conn.commit()
    invalidate_cache()


async def get_settings(key: str) -> str | None:
    async with db.acquire() as conn:
        return await conn.fetchval("SELECT value FROM settings WHERE key=?", key)


async def get_all_settings() -> dict:
    global _settings_cache, _settings_cache_time
    now = time.time()
    if _settings_cache is not None and (now - _settings_cache_time) < _SETTINGS_CACHE_TTL:
        return _settings_cache
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM settings")
    _settings_cache = {r["key"]: r["value"] for r in rows}
    _settings_cache_time = now
    return _settings_cache
