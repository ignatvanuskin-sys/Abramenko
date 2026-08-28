"""Read-only booking statistics repository."""
from __future__ import annotations

import time

import db

_stats_cache: dict | None = None
_stats_cache_time = 0.0
_STATS_CACHE_TTL = 15


def invalidate_stats_cache() -> None:
    global _stats_cache, _stats_cache_time
    _stats_cache = None
    _stats_cache_time = 0.0


async def get_stats() -> dict:
    global _stats_cache, _stats_cache_time
    now = time.time()
    if _stats_cache is not None and (now - _stats_cache_time) < _STATS_CACHE_TTL:
        return _stats_cache
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as total, "
            "COUNT(*) FILTER (WHERE status='active') as active, "
            "COUNT(*) FILTER (WHERE status='cancelled') as cancelled, "
            "COUNT(*) FILTER (WHERE status='completed') as completed, "
            "COALESCE(SUM(price) FILTER (WHERE status='completed'), 0) as revenue "
            "FROM bookings"
        )
        _stats_cache = {
            "total": row["total"] or 0,
            "active": row["active"] or 0,
            "cancelled": row["cancelled"] or 0,
            "completed": row["completed"] or 0,
            "revenue": int(row["revenue"] or 0),
        }
        _stats_cache_time = now
        return _stats_cache


async def get_stats_by_day() -> list[dict]:
    async with db.acquire() as conn:
        return await conn.fetch(
            "SELECT date, COUNT(*) as count FROM bookings "
            "WHERE status IN ('active', 'completed') GROUP BY date ORDER BY date"
        )


async def get_stats_by_service() -> list[dict]:
    async with db.acquire() as conn:
        return await conn.fetch(
            "SELECT service, COUNT(*) as count, SUM(price) as revenue "
            "FROM bookings WHERE status IN ('active', 'completed') GROUP BY service"
        )


async def get_service_stats(service_name: str) -> dict:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active, "
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed, "
            "SUM(CASE WHEN status='completed' THEN price ELSE 0 END) as revenue "
            "FROM bookings WHERE service=?",
            service_name,
        )
        return {
            "total": row["total"] or 0,
            "active": row["active"] or 0,
            "completed": row["completed"] or 0,
            "revenue": row["revenue"] or 0,
        }


async def get_active_bookings_count() -> int:
    async with db.acquire() as conn:
        return (await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE status='active'")) or 0


async def get_bookings_summary(date_str: str) -> dict:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as total, "
            "COALESCE(SUM(CASE WHEN status='active' THEN 1 ELSE 0 END),0) as active, "
            "COALESCE(SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END),0) as cancelled, "
            "COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) as completed, "
            "COALESCE(SUM(CASE WHEN status='completed' THEN price ELSE 0 END),0) as revenue "
            "FROM bookings WHERE date=?",
            date_str,
        )
        return {
            "total": row["total"] or 0,
            "active": row["active"] or 0,
            "cancelled": row["cancelled"] or 0,
            "completed": row["completed"] or 0,
            "revenue": row["revenue"] or 0,
        }
