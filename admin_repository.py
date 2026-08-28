"""Administrator audit-log repository."""
from __future__ import annotations

import config
import db
from tz_utils import get_now


async def log_admin_action(
    admin_id: int,
    action: str,
    entity_type: str = "",
    entity_id: str = "",
    old_value: str = "",
    new_value: str = "",
) -> None:
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO admin_audit_log "
            "(admin_id, action, entity_type, entity_id, old_value, new_value, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            admin_id,
            action,
            entity_type,
            str(entity_id or ""),
            old_value or "",
            new_value or "",
            get_now(config.TIMEZONE).isoformat(),
        )
        await conn.commit()


async def get_admin_audit_log(limit: int = 50, offset: int = 0) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    async with db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM admin_audit_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
            limit,
            offset,
        )
