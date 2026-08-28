"""Persistent counters and structured events for production observability.

The repository deliberately depends only on the shared DB abstraction. This keeps
metrics storage independent from booking and user-domain persistence.
"""
from __future__ import annotations

from datetime import datetime, timezone

import db


async def ensure_schema() -> None:
    """Create observability tables idempotently on both supported databases."""
    async with db.acquire() as conn:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS metrics_counters (
                metric_name TEXT PRIMARY KEY,
                value BIGINT NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        events_id = "BIGSERIAL PRIMARY KEY" if db.is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        await conn.execute(
            f"""CREATE TABLE IF NOT EXISTS metric_events (
                id {events_id},
                event_name TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT NOT NULL
            )"""
        )
        if db.is_postgres():
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_metric_events_created_at ON metric_events(created_at)")
        else:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_metric_events_created_at ON metric_events(created_at)")
        await conn.commit()


async def increment_counter(name: str, amount: int = 1) -> None:
    """Atomically increment a counter and keep its update timestamp."""
    now = datetime.now(timezone.utc).isoformat()
    if db.is_postgres():
        sql = (
            "INSERT INTO metrics_counters(metric_name, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(metric_name) DO UPDATE SET value=metrics_counters.value + EXCLUDED.value, "
            "updated_at=EXCLUDED.updated_at"
        )
    else:
        sql = (
            "INSERT INTO metrics_counters(metric_name, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(metric_name) DO UPDATE SET value=value + excluded.value, updated_at=excluded.updated_at"
        )
    async with db.acquire() as conn:
        await conn.execute(sql, name, int(amount), now)
        await conn.commit()


async def load_counters() -> dict[str, int]:
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT metric_name, value FROM metrics_counters")
    return {str(row["metric_name"]): int(row["value"] or 0) for row in rows}


async def record_event(event_name: str, payload: str, created_at: str | None = None) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO metric_events(event_name, payload, created_at) VALUES(?, ?, ?)",
            event_name,
            payload,
            created_at or datetime.now(timezone.utc).isoformat(),
        )
        await conn.commit()


async def prune_events(retention_days: int = 30) -> int:
    """Delete old event details while retaining aggregate counters."""
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).isoformat()
    async with db.acquire() as conn:
        count = await conn.execute_count("DELETE FROM metric_events WHERE created_at < ?", cutoff)
        return int(count)
