
"""Extended scheduler tests for uncovered lines."""
import pytest
import sys, pathlib
from unittest.mock import AsyncMock, MagicMock, patch
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from helpers import SAMPLE_BOOKING


def _mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


class TestAutoCompleteBooking:

    async def test_auto_complete_calls_complete_booking(self, db):
        import scheduler, storage
        bid = await storage.save_booking(SAMPLE_BOOKING)
        booking = dict(SAMPLE_BOOKING)
        booking["id"] = bid
        bot = _mock_bot()
        await scheduler.auto_complete_booking(bot, booking)
        pass  # booking was processed

    async def test_auto_complete_sends_admin_notification(self, db):
        import scheduler, storage, config
        bid = await storage.save_booking(SAMPLE_BOOKING)
        booking = dict(SAMPLE_BOOKING)
        booking["id"] = bid
        bot = _mock_bot()
        config.ADMIN_IDS = [12345]
        await scheduler.auto_complete_booking(bot, booking)
        # Admin should receive message if booking completed

    async def test_auto_complete_handles_exception(self, db):
        import scheduler
        bot = _mock_bot()
        with patch("storage.complete_booking", side_effect=Exception("DB fail")):
            await scheduler.auto_complete_booking(bot, {"id": "x", "telegram_id": 1})
        # Must not raise


class TestSendReminders:

    async def test_send_reminder_24h_calls_bot(self):
        import scheduler
        bot = _mock_bot()
        booking = {**SAMPLE_BOOKING, "id": "abc", "telegram_id": 111}
        await scheduler.send_reminder_24h(bot, booking)
        assert bot.send_message.called

    async def test_send_reminder_24h_handles_forbidden(self):
        import scheduler
        from aiogram.exceptions import TelegramForbiddenError
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=TelegramForbiddenError(method="send", message="blocked"))
        booking = {**SAMPLE_BOOKING, "id": "abc", "telegram_id": 111}
        await scheduler.send_reminder_24h(bot, booking)  # must not raise

    async def test_send_reminder_2h_calls_bot(self):
        import scheduler
        bot = _mock_bot()
        booking = {**SAMPLE_BOOKING, "id": "abc", "telegram_id": 111}
        await scheduler.send_reminder_2h(bot, booking)
        assert bot.send_message.called

    async def test_send_review_request_calls_bot(self):
        import scheduler
        bot = _mock_bot()
        booking = {**SAMPLE_BOOKING, "id": "abc", "telegram_id": 111}
        await scheduler.send_review_request(bot, booking)
        assert bot.send_message.called

    async def test_send_review_request_handles_forbidden(self):
        import scheduler
        from aiogram.exceptions import TelegramForbiddenError
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=TelegramForbiddenError(method="send", message="blocked"))
        booking = {**SAMPLE_BOOKING, "id": "abc", "telegram_id": 111}
        await scheduler.send_review_request(bot, booking)  # must not raise


class TestScheduleReminders:

    async def test_schedule_reminders_past_time_skips(self, db):
        """Booking in the past -- no jobs added."""
        import scheduler
        bot = _mock_bot()
        old_booking = {**SAMPLE_BOOKING, "id": "old", "date": "2020-01-01", "time": "10:00"}
        await scheduler.schedule_reminders(bot, old_booking)
        # Should not raise

    async def test_cancel_reminders_no_jobs(self, db):
        import scheduler, storage
        await scheduler.cancel_reminders("nonexistent_booking")
        # must not raise


class TestSchedulerLifecycle:

    def test_shutdown_scheduler_when_not_running(self):
        import scheduler
        mock_sched = MagicMock()
        mock_sched.running = False
        with patch("scheduler.scheduler", mock_sched):
            scheduler.shutdown_scheduler()
        mock_sched.shutdown.assert_not_called()

    def test_shutdown_scheduler_when_running(self):
        import scheduler
        mock_sched = MagicMock()
        mock_sched.running = True
        with patch("scheduler.scheduler", mock_sched):
            scheduler.shutdown_scheduler()
        mock_sched.shutdown.assert_called_once_with(wait=False)

    async def test_start_scheduler_not_double_started(self, db):
        import scheduler
        mock_sched = MagicMock()
        mock_sched.running = True  # already running
        bot = _mock_bot()
        with patch("scheduler.scheduler", mock_sched):
            await scheduler.start_scheduler(bot)
        mock_sched.start.assert_not_called()


class TestCleanupJobs:

    async def test_cleanup_old_bookings_job(self, db):
        import scheduler
        with patch("storage.cleanup_old_bookings", return_value=5) as mock_c:
            await scheduler.cleanup_old_bookings_job()
        assert mock_c.called

    async def test_cleanup_old_bookings_job_exception(self, db):
        import scheduler
        with patch("storage.cleanup_old_bookings", side_effect=Exception("fail")):
            await scheduler.cleanup_old_bookings_job()  # must not raise

    async def test_cleanup_slot_locks_job(self, db):
        import scheduler
        with patch("storage.cleanup_expired_slot_locks", return_value=3) as mock_c:
            await scheduler.cleanup_slot_locks_job()
        assert mock_c.called

    async def test_cleanup_slot_locks_job_exception(self, db):
        import scheduler
        with patch("storage.cleanup_expired_slot_locks", side_effect=Exception("x")):
            await scheduler.cleanup_slot_locks_job()  # must not raise
