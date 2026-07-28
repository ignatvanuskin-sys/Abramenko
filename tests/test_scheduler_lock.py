import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock


async def test_scheduler_lock_acquire_release(db):
    import storage

    assert await storage.acquire_scheduler_lock("scheduler", "owner-a", ttl_seconds=60) is True
    assert await storage.acquire_scheduler_lock("scheduler", "owner-b", ttl_seconds=60) is False
    assert await storage.release_scheduler_lock("scheduler", "owner-b") is False
    assert await storage.release_scheduler_lock("scheduler", "owner-a") is True
    assert await storage.acquire_scheduler_lock("scheduler", "owner-b", ttl_seconds=60) is True


async def test_scheduler_lock_expired_can_be_acquired(db):
    import storage
    import db as dbmod
    import config
    from tz_utils import get_now

    assert await storage.acquire_scheduler_lock("scheduler", "stale-owner", ttl_seconds=60) is True
    expired_at = (get_now(config.TIMEZONE) - timedelta(seconds=1)).isoformat()
    async with dbmod.acquire() as conn:
        await conn.execute(
            "UPDATE scheduler_locks SET expires_at=? WHERE lock_name=?",
            expired_at,
            "scheduler",
        )
        await conn.commit()

    assert await storage.acquire_scheduler_lock("scheduler", "new-owner", ttl_seconds=60) is True
    status = await storage.get_scheduler_lock_status("scheduler")
    assert status["locked"] is True
    assert status["owner"] == "new-owner"


async def test_scheduler_lock_concurrent_acquire(db):
    import storage

    results = await asyncio.gather(
        storage.acquire_scheduler_lock("scheduler", "owner-a", ttl_seconds=60),
        storage.acquire_scheduler_lock("scheduler", "owner-b", ttl_seconds=60),
    )
    assert results.count(True) == 1
    assert results.count(False) == 1


async def test_persisted_scheduler_job_runs_once(db):
    import scheduler
    import storage

    callback = AsyncMock()
    await storage.save_scheduler_job("job-1", "2099-01-01T10:00:00+06:00", "reminder_24h", "booking-1")

    assert await scheduler._run_persisted_scheduler_job("job-1", "reminder_24h", callback) is True
    assert callback.await_count == 1

    assert await scheduler._run_persisted_scheduler_job("job-1", "reminder_24h", callback) is True
    assert callback.await_count == 1


async def test_scheduler_lock_busy_skips_tick(db):
    import scheduler
    import storage

    callback = AsyncMock()
    await storage.acquire_scheduler_lock("scheduler_periodic:test", "other-owner", ttl_seconds=60)

    result = await scheduler._run_with_scheduler_lock("scheduler_periodic:test", "test", callback)

    assert result is False
    callback.assert_not_awaited()
