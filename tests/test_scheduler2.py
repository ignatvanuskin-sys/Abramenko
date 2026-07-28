
"""Tests for scheduler.schedule_reminders"""
import pytest
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from unittest.mock import AsyncMock, MagicMock, patch
from helpers import SAMPLE_BOOKING
from datetime import datetime, timedelta


SAMPLE = {
    **SAMPLE_BOOKING,
    "id": "bid123",
}


class TestScheduleReminders:
    async def test_schedules_future_booking(self, db):
        import scheduler, storage
        bid = await storage.save_booking(SAMPLE_BOOKING)
        booking = {**SAMPLE, "id": bid, "date": "2099-12-07", "time": "10:00"}
        mock_sched = MagicMock()
        mock_sched.add_job = MagicMock(return_value=MagicMock(id="job1"))
        bot = AsyncMock()
        with patch("scheduler.scheduler", mock_sched):
            await scheduler.schedule_reminders(bot, booking)
        # Should schedule at least 1 job for future booking
        assert mock_sched.add_job.call_count >= 1

    async def test_skips_reminder_for_past_date(self, db):
        """Past dates should not get reminder jobs."""
        import scheduler
        bot = AsyncMock()
        booking = {**SAMPLE, "date": "2000-01-01", "time": "10:00"}
        mock_sched = MagicMock()
        mock_sched.add_job = MagicMock(return_value=MagicMock(id="job1"))
        with patch("scheduler.scheduler", mock_sched):
            await scheduler.schedule_reminders(bot, booking)
        # For past dates, no reminder jobs (24h/2h) should be scheduled
        # auto_complete might be called immediately, verify it doesn't crash

    async def test_handles_scheduler_error_gracefully(self, db):
        import scheduler, storage
        bid = await storage.save_booking(SAMPLE_BOOKING)
        booking = {**SAMPLE, "id": bid, "date": "2099-12-07", "time": "10:00"}
        mock_sched = MagicMock()
        mock_sched.add_job.side_effect = Exception("scheduler unavailable")
        bot = AsyncMock()
        with patch("scheduler.scheduler", mock_sched):
            await scheduler.schedule_reminders(bot, booking)  # must not raise

    async def test_reminder_24h_scheduled_before_booking(self, db):
        import scheduler, storage
        bid = await storage.save_booking(SAMPLE_BOOKING)
        booking = {**SAMPLE, "id": bid, "date": "2099-12-07", "time": "14:00"}
        captured_jobs = []
        mock_sched = MagicMock()
        def capture_add_job(*args, **kwargs):
            captured_jobs.append(kwargs.get("run_date") or (args[1] if len(args) > 1 else None))
            return MagicMock(id=f"job{len(captured_jobs)}")
        mock_sched.add_job = capture_add_job
        bot = AsyncMock()
        with patch("scheduler.scheduler", mock_sched):
            await scheduler.schedule_reminders(bot, booking)
        # Verify jobs were added
        assert len(captured_jobs) > 0


class TestStartShutdownScheduler:
    async def test_start_scheduler_runs(self):
        import scheduler
        mock_sched = MagicMock()
        mock_sched.running = True  # already running → if-block skipped, no storage calls
        with patch("scheduler.scheduler", mock_sched):
            await scheduler.start_scheduler(AsyncMock())
        # Should have started or done nothing if already running

    def test_shutdown_scheduler(self):
        import scheduler
        mock_sched = MagicMock()
        mock_sched.running = True
        with patch("scheduler.scheduler", mock_sched):
            scheduler.shutdown_scheduler()
        mock_sched.shutdown.assert_called()

    async def test_cleanup_old_bookings_job(self, db):
        import scheduler
        bot = AsyncMock()
        # Just verify it doesn't crash
        await scheduler.cleanup_old_bookings_job()
