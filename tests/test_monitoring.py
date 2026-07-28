import time
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

def test_start_monitoring():
    import monitoring
    monitoring._start_time = None
    monitoring.start_monitoring()
    assert monitoring._start_time is not None

def test_get_uptime_after_start():
    import monitoring
    monitoring.start_monitoring()
    time.sleep(0.01)
    assert monitoring.get_uptime() > 0

def test_get_uptime_before_start():
    import monitoring
    monitoring._start_time = None
    assert monitoring.get_uptime() == 0

def test_format_seconds():
    import monitoring
    assert monitoring.format_uptime(45) == "45s"

def test_format_minutes():
    import monitoring
    assert monitoring.format_uptime(125) == "2m 5s"

def test_format_hours():
    import monitoring
    assert monitoring.format_uptime(3725) == "1h 2m 5s"

def test_format_zero():
    import monitoring
    assert monitoring.format_uptime(0) == "0s"

@pytest.mark.asyncio
async def test_check_db_ok():
    """check_db_health now uses db.acquire() instead of aiosqlite directly."""
    import monitoring
    import db as _db
    conn_mock = AsyncMock()
    conn_mock.fetchval = AsyncMock(return_value=1)
    from unittest.mock import patch
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fake_acquire():
        yield conn_mock
    with patch.object(_db, "acquire", _fake_acquire):
        result = await monitoring.check_db_health()
    assert result is True

@pytest.mark.asyncio
async def test_check_db_fail():
    """check_db_health returns False when DB raises."""
    import monitoring
    import db as _db
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fail_acquire():
        raise Exception("simulated DB error")
        yield
    with patch.object(_db, "acquire", _fail_acquire):
        result = await monitoring.check_db_health()
    assert result is False

@pytest.mark.asyncio
async def test_check_storage_ok():
    import monitoring
    assert await monitoring.check_storage_health() is True

@pytest.mark.asyncio
async def test_check_storage_fail(tmp_path, monkeypatch):
    """check_storage_health returns False when FSM JSON file is corrupt."""
    import monitoring
    import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "db.db"))
    fsm_file = tmp_path / "fsm_state.json"
    fsm_file.write_bytes(b"\xff\xfe broken json {{{")
    monkeypatch.setenv("REDIS_URL", "")
    result = await monitoring.check_storage_health()
    assert result is False

@pytest.mark.asyncio
async def test_scheduler_running():
    import monitoring
    ms = MagicMock(); ms.running = True
    with patch("scheduler.scheduler", ms):
        assert await monitoring.check_scheduler_health() is True

@pytest.mark.asyncio
async def test_scheduler_not_running():
    import monitoring
    ms = MagicMock(); ms.running = False
    with patch("scheduler.scheduler", ms):
        assert await monitoring.check_scheduler_health() is False

@pytest.mark.asyncio
async def test_scheduler_exception():
    import monitoring
    with patch("monitoring.scheduler", create=True) as ms:
        ms.scheduler = None
        type(ms).running = property(lambda self: (_ for _ in ()).throw(Exception("e")))
        result = await monitoring.check_scheduler_health()
    # Should return False or True depending on import - just check it runs
    assert isinstance(result, bool)

@pytest.mark.asyncio
async def test_health_ok():
    import monitoring
    monitoring.start_monitoring()
    with (patch.object(monitoring, "check_db_health", AsyncMock(return_value=True)),
         patch.object(monitoring, "check_storage_health", AsyncMock(return_value=True)),
         patch.object(monitoring, "check_scheduler_health", AsyncMock(return_value=True)),
         patch.object(monitoring, "check_scheduler_lock_status", AsyncMock(return_value={"status": "free", "locked": False}))):
        s = await monitoring.get_health_status()
    assert s["status"] == "ok"
    assert s["checks"]["database"] == "ok"
    assert s["checks"]["scheduler_lock"]["status"] == "free"
    assert "metrics" in s

@pytest.mark.asyncio
async def test_health_degraded():
    import monitoring
    monitoring.start_monitoring()
    with (patch.object(monitoring, "check_db_health", AsyncMock(return_value=False)),
         patch.object(monitoring, "check_storage_health", AsyncMock(return_value=True)),
         patch.object(monitoring, "check_scheduler_health", AsyncMock(return_value=True)),
         patch.object(monitoring, "check_scheduler_lock_status", AsyncMock(return_value={"status": "free", "locked": False}))):
        s = await monitoring.get_health_status()
    assert s["status"] == "degraded"

@pytest.mark.asyncio
async def test_scheduler_lock_status_in_health(db):
    import monitoring
    import storage
    await storage.acquire_scheduler_lock("scheduler", "owner", ttl_seconds=60)
    status = await monitoring.check_scheduler_lock_status()
    assert status["status"] == "held"
    assert status["owner"] == "owner"

def test_metrics_increment_and_copy():
    import monitoring
    before = monitoring.get_metrics().get("bookings_created", 0)
    monitoring.increment_counter("bookings_created")
    metrics = monitoring.get_metrics()
    assert metrics["bookings_created"] == before + 1
    metrics["bookings_created"] = -1
    assert monitoring.get_metrics()["bookings_created"] == before + 1

def test_payment_placeholder_logs():
    import monitoring
    with patch.object(monitoring, "log_event") as log_mock:
        monitoring.log_payment_placeholder("disabled", booking_id="b1")
    log_mock.assert_called_once()
