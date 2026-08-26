# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Demo-request persistence through the application's shared DB architecture."""

import json
import uuid
from datetime import datetime, timezone

import db as _db


_SCHEMA_READY = False


async def init_demo_repository() -> None:
    """Create the demo table in the same PostgreSQL/SQLite store as the app."""
    global _SCHEMA_READY
    async with _db.acquire() as conn:
        if _db.is_postgres():
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS demo_requests (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    request_type TEXT NOT NULL,
                    telegram_id BIGINT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_confirmation',
                    notification_status TEXT NOT NULL DEFAULT 'pending',
                    notification_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )"""
            )
        else:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS demo_requests (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    request_type TEXT NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_confirmation',
                    notification_status TEXT NOT NULL DEFAULT 'pending',
                    notification_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
        await conn.commit()
    _SCHEMA_READY = True


async def _ensure_schema() -> None:
    if not _SCHEMA_READY:
        await init_demo_repository()


async def create_or_get_request(request_type: str, telegram_id: int, payload: dict, idempotency_key: str) -> dict:
    """Atomically insert once and return the row selected by idempotency key."""
    await _ensure_schema()
    now = datetime.now(timezone.utc)
    row_id = str(uuid.uuid4())
    async with _db.acquire() as conn:
        await conn.execute(
            """INSERT INTO demo_requests
               (id, idempotency_key, request_type, telegram_id, payload_json, status,
                notification_status, notification_error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (idempotency_key) DO NOTHING""",
            row_id, idempotency_key, request_type, telegram_id,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "pending_confirmation", "pending", None, now, now,
        )
        await conn.commit()
        row = await conn.fetchrow(
            "SELECT * FROM demo_requests WHERE idempotency_key = ?", idempotency_key
        )
    if row is None:
        raise RuntimeError("demo request could not be created or loaded")
    return _as_dict(row)


async def update_notification(request_id: str, status: str, error: str | None = None) -> None:
    await _ensure_schema()
    async with _db.acquire() as conn:
        await conn.execute(
            """UPDATE demo_requests
               SET notification_status = ?, notification_error = ?, updated_at = ?
               WHERE id = ?""",
            status, error, datetime.now(timezone.utc), request_id,
        )
        await conn.commit()


async def claim_notification(request_id: str) -> bool:
    """Claim a pending delivery atomically; concurrent confirm/retry gets False."""
    await _ensure_schema()
    async with _db.acquire() as conn:
        count = await conn.execute_count(
            """UPDATE demo_requests SET notification_status = 'sending',
               notification_error = NULL, updated_at = ?
               WHERE id = ? AND notification_status IN ('pending', 'failed')""",
            datetime.now(timezone.utc), request_id,
        )
        return count == 1


async def pending_notifications() -> list[dict]:
    await _ensure_schema()
    async with _db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM demo_requests WHERE notification_status IN ('pending','failed') ORDER BY created_at"
        )
    return [_as_dict(row) for row in rows]


async def get_request(request_id: str) -> dict | None:
    await _ensure_schema()
    async with _db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM demo_requests WHERE id = ?", request_id)
    return _as_dict(row) if row else None


def _as_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "request_type": row["request_type"],
        "telegram_id": row["telegram_id"],
        "payload": json.loads(row["payload_json"]),
        "status": row["status"],
        "notification_status": row["notification_status"],
        "notification_error": row["notification_error"],
    }
