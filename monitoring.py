# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
_persist_tasks: set[asyncio.Task] = set()

_start_time = None
_COUNTERS = {
    "bookings_created": 0,
    "bookings_cancelled": 0,
    "bookings_completed": 0,
    "bookings_rejected": 0,
    "reminders_sent": 0,
    "reminders_failed": 0,
    "backup_success": 0,
    "backup_failed": 0,
    "broadcast_sent": 0,
    "broadcast_failed": 0,
    "errors_total": 0,
    "api_retries": 0,
    "webhook_calls": 0,
    "rate_limit_hits": 0,
    "referrals_completed": 0,
    "reviews_submitted": 0,
    "waitlist_added": 0,
    "fsm_state_cleared": 0,
    "slot_locks_acquired": 0,
    "slot_locks_expired": 0,
}


def _track_persist_task(task: asyncio.Task) -> None:
    _persist_tasks.add(task)
    task.add_done_callback(_persist_tasks.discard)
    task.add_done_callback(_log_persist_failure)


def _log_persist_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.warning("Persistent monitoring write failed: %s", task.exception())


def increment_counter(name: str, amount: int = 1) -> None:
    _COUNTERS[name] = _COUNTERS.get(name, 0) + amount
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    from metrics_repository import increment_counter as persist_counter
    _track_persist_task(loop.create_task(persist_counter(name, amount)))


async def initialize_persistent_monitoring() -> None:
    """Create observability tables and restore counters after a restart."""
    from metrics_repository import ensure_schema, load_counters
    await ensure_schema()
    _COUNTERS.update(await load_counters())
    logger.info("Persistent monitoring initialized with %d counters", len(_COUNTERS))


async def shutdown_monitoring() -> None:
    if _persist_tasks:
        await asyncio.gather(*list(_persist_tasks), return_exceptions=True)


def get_metrics() -> dict:
    return dict(_COUNTERS)


def get_metrics_snapshot() -> dict:
    """Returns counters + computed ratios for admin dashboard."""
    c = dict(_COUNTERS)
    created = c.get("bookings_created", 0)
    cancelled = c.get("bookings_cancelled", 0)
    errors = c.get("errors_total", 0)
    total_ops = created + cancelled + errors or 1
    c["error_rate_pct"] = round(errors / total_ops * 100, 1)
    if created > 0:
        c["cancel_rate_pct"] = round(cancelled / created * 100, 1)
    else:
        c["cancel_rate_pct"] = 0.0
    c["uptime_seconds"] = round(get_uptime(), 1)
    c["uptime_human"] = format_uptime(c["uptime_seconds"])
    return c


def log_event(logger_obj, event: str, **fields) -> None:
    payload = {"event": event, **fields}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    logger_obj.info(encoded)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    from metrics_repository import record_event
    _track_persist_task(loop.create_task(record_event(event, encoded)))


def log_payment_placeholder(action: str = "not_implemented", **fields) -> None:
    log_event(logger, "payment_placeholder", action=action, **fields)


def start_monitoring():
    global _start_time
    _start_time = time.time()
    logger.info("Monitoring started")


def get_uptime() -> float:
    if _start_time:
        return time.time() - _start_time
    return 0


async def check_db_health() -> bool:
    """Quick database health check (read only). Fast enough for frequent calls."""
    try:
        import db as _db
        async with _db.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
            return val == 1
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return False


async def check_db_deep_health() -> dict:
    """Deep database health check with write verification (for troubleshooting, not /health)."""
    result = {"read_ok": False, "write_ok": False, "error": ""}
    try:
        import db as _db
        async with _db.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
            result["read_ok"] = (val == 1)
            if result["read_ok"]:
                await conn.execute(
                    "INSERT INTO settings (key, value) VALUES ('_health_check', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value='1'"
                )
                await conn.commit()
                await conn.execute("DELETE FROM settings WHERE key='_health_check'")
                await conn.commit()
                result["write_ok"] = True
    except Exception as e:
        result["error"] = str(e)
    return result


async def check_storage_health() -> bool:
    """HIGH-05 FIX: Actually check the FSM storage (FileStorage or Redis)."""
    try:
        import os
        import config as _cfg
        redis_url = os.getenv("REDIS_URL", "")
        if redis_url:
            # M-3 FIX: guard aioredis import in case package is not installed
            try:
                import aioredis
            except ImportError:
                required = bool(getattr(_cfg, "REQUIRE_REDIS_FSM", False))
                logger.error("Redis URL is configured but aioredis is unavailable")
                return False if required else True
            r = await aioredis.from_url(redis_url, socket_connect_timeout=3)
            await r.ping()
            await r.close()
        else:
            # Test FileStorage JSON file is readable/writable
            from pathlib import Path
            fsm_file = Path(_cfg.DB_PATH).parent / "fsm_state.json"
            parent = fsm_file.parent
            parent.mkdir(parents=True, exist_ok=True)
            if fsm_file.exists():
                with open(fsm_file, "r", encoding="utf-8") as f:
                    import json
                    json.load(f)
        return True
    except Exception as e:
        logger.error(f"Storage health check failed: {e}")
        return False


async def check_scheduler_health() -> bool:
    """Check if scheduler is running."""
    try:
        from scheduler import scheduler
        return scheduler.running
    except Exception as e:
        logger.error(f"Scheduler health check failed: {e}")
        return False


async def check_scheduler_lock_status() -> dict:
    try:
        import storage
        return await storage.get_scheduler_lock_status("scheduler")
    except Exception as e:
        logger.error(f"Scheduler lock status check failed: {e}")
        return {"lock_name": "scheduler", "locked": False, "status": "error", "error": str(e)}


async def get_health_status() -> dict:
    """Get comprehensive health status with real checks."""
    uptime = get_uptime()
    db_ok = await check_db_health()
    storage_ok = await check_storage_health()
    scheduler_ok = await check_scheduler_health()
    scheduler_lock = await check_scheduler_lock_status()
    lock_ok = scheduler_lock.get("status") != "error"
    all_ok = db_ok and storage_ok and scheduler_ok and lock_ok
    return {
        "status": "ok" if all_ok else "degraded",
        "uptime_seconds": round(uptime, 2),
        "uptime_human": format_uptime(uptime),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": get_metrics(),
        "checks": {
            "database": "ok" if db_ok else "error",
            "storage": "ok" if storage_ok else "error",
            "scheduler": "ok" if scheduler_ok else "error",
            "scheduler_lock": scheduler_lock,
        }
    }


def format_uptime(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"
