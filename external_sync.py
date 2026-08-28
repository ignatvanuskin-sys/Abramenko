"""External calendar/CRM synchronization via an authenticated REST webhook.

The adapter is intentionally provider-agnostic: Google Calendar, Bitrix24,
amocrm, Make/Zapier or a private integration can consume the same event
contract without coupling booking persistence to a vendor SDK.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import aiohttp

import config

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(getattr(config, "EXTERNAL_SYNC_URL", "").strip())


def _payload(event: str, booking: dict[str, Any], booking_id: str) -> dict[str, Any]:
    return {
        "event": event,
        "booking_id": booking_id,
        "branch": booking.get("branch", booking.get("master_key", "default")),
        "date": booking.get("date"),
        "time": booking.get("time"),
        "master": booking.get("master"),
        "service": booking.get("service"),
        "duration_minutes": booking.get("duration_minutes", 30),
        "price": booking.get("price"),
        "name": booking.get("name"),
        "phone": booking.get("phone"),
        "status": booking.get("status", "active" if event == "booking.created" else "cancelled"),
    }


async def sync_booking(event: str, booking: dict[str, Any], booking_id: str) -> bool:
    """Send a booking lifecycle event; return False without breaking bot flow."""
    if not _enabled():
        return True
    body = json.dumps(_payload(event, booking, booking_id), ensure_ascii=False, separators=(",", ":"))
    secret = getattr(config, "EXTERNAL_SYNC_SECRET", "").encode()
    signature = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest() if secret else ""
    headers = {"Content-Type": "application/json", "User-Agent": "nail-tg-sync/1.0"}
    if signature:
        headers["X-Signature-SHA256"] = signature
    timeout = aiohttp.ClientTimeout(total=getattr(config, "EXTERNAL_SYNC_TIMEOUT", 8))
    retries = max(1, int(getattr(config, "EXTERNAL_SYNC_RETRIES", 3)))
    for attempt in range(1, retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(config.EXTERNAL_SYNC_URL, data=body.encode(), headers=headers) as response:
                    if 200 <= response.status < 300:
                        logger.info("external_sync_success event=%s booking_id=%s", event, booking_id)
                        return True
                    response_text = await response.text()
                    logger.warning("external_sync_http_error event=%s booking_id=%s status=%s body=%s", event, booking_id, response.status, response_text[:200])
        except Exception as exc:
            logger.warning("external_sync_attempt_failed event=%s booking_id=%s attempt=%s error=%s", event, booking_id, attempt, exc)
    logger.error("external_sync_failed event=%s booking_id=%s attempts=%s", event, booking_id, retries)
    return False


async def sync_booking_created(booking: dict[str, Any], booking_id: str) -> bool:
    return await sync_booking("booking.created", booking, booking_id)


async def sync_booking_cancelled(booking: dict[str, Any], booking_id: str) -> bool:
    return await sync_booking("booking.cancelled", booking, booking_id)
