import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


class TestAutoCompleteBookingEdges:

    async def test_loyalty_milestone_reward_sent(self, mock_bot):
        import scheduler as sched
        import config
        completed = {"id": "bk1", "date": "2099-01-01", "time": "10:00",
                     "name": "Test", "telegram_id": 999, "master": "A",
                     "service": "Cut", "price": 3000, "status": "completed"}
        milestone = config.LOYALTY_VISIT_INTERVAL
        with patch("scheduler.storage.complete_booking", new_callable=AsyncMock, return_value=completed), \
             patch("scheduler.storage.update_loyalty", new_callable=AsyncMock, return_value=milestone), \
             patch("scheduler.config.ADMIN_IDS", []):
            await sched.auto_complete_booking(mock_bot, {"id": "bk1"})
        mock_bot.send_message.assert_called()

    async def test_loyalty_update_exception_handled(self, mock_bot):
        import scheduler as sched
        completed = {"id": "bk2", "date": "2099-01-01", "time": "10:00",
                     "name": "Test", "telegram_id": 888, "master": "A",
                     "service": "Cut", "price": 3000}
        with patch("scheduler.storage.complete_booking", new_callable=AsyncMock, return_value=completed), \
             patch("scheduler.storage.update_loyalty", new_callable=AsyncMock, side_effect=Exception("db error")), \
             patch("scheduler.config.ADMIN_IDS", []):
            await sched.auto_complete_booking(mock_bot, {"id": "bk2"})

    async def test_loyalty_reward_send_exception(self, mock_bot):
        import scheduler as sched
        import config
        completed = {"id": "bk3", "date": "2099-01-01", "time": "10:00",
                     "name": "Test", "telegram_id": 777, "master": "A",
                     "service": "Cut", "price": 3000}
        milestone = config.LOYALTY_VISIT_INTERVAL
        mock_bot.send_message = AsyncMock(side_effect=Exception("send error"))
        with patch("scheduler.storage.complete_booking", new_callable=AsyncMock, return_value=completed), \
             patch("scheduler.storage.update_loyalty", new_callable=AsyncMock, return_value=milestone), \
             patch("scheduler.config.ADMIN_IDS", []):
            await sched.auto_complete_booking(mock_bot, {"id": "bk3"})

    async def test_complete_booking_returns_none(self, mock_bot):
        import scheduler as sched
        with patch("scheduler.storage.complete_booking", new_callable=AsyncMock, return_value=None):
            await sched.auto_complete_booking(mock_bot, {"id": "bkNone"})
        mock_bot.send_message.assert_not_called()

    async def test_complete_booking_raises_exception(self, mock_bot):
        import scheduler as sched
        with patch("scheduler.storage.complete_booking", new_callable=AsyncMock, side_effect=Exception("err")):
            await sched.auto_complete_booking(mock_bot, {"id": "bkErr"})


class TestReminderEdgeCases:

    async def test_reminder_24h_retry_after(self, mock_bot):
        from aiogram.exceptions import TelegramRetryAfter
        import scheduler as sched
        booking = {"id": "bk5", "date": "2099-01-01", "time": "10:00",
                   "telegram_id": 111, "master": "A", "service": "Cut"}
        mock_bot.send_message = AsyncMock(side_effect=TelegramRetryAfter(method=MagicMock(), message="Too Many Requests", retry_after=10))
        with patch("scheduler.keyboards._format_date", return_value="01.01.2099"), \
             patch("scheduler.keyboards.remind_kb", return_value=MagicMock()):
            await sched.send_reminder_24h(mock_bot, booking)

    async def test_reminder_24h_generic_exception(self, mock_bot):
        import scheduler as sched
        booking = {"id": "bk6", "date": "2099-01-01", "time": "10:00",
                   "telegram_id": 111, "master": "A", "service": "Cut"}
        mock_bot.send_message = AsyncMock(side_effect=Exception("net error"))
        with patch("scheduler.keyboards._format_date", return_value="01.01.2099"), \
             patch("scheduler.keyboards.remind_kb", return_value=MagicMock()):
            await sched.send_reminder_24h(mock_bot, booking)

    async def test_reminder_2h_retry_after(self, mock_bot):
        from aiogram.exceptions import TelegramRetryAfter
        import scheduler as sched
        booking = {"id": "bk7", "date": "2099-01-01", "time": "10:00",
                   "telegram_id": 111, "master": "A", "service": "Cut"}
        mock_bot.send_message = AsyncMock(side_effect=TelegramRetryAfter(method=MagicMock(), message="Too Many Requests", retry_after=5))
        with patch("scheduler.keyboards._format_date", return_value="01.01.2099"), \
             patch("scheduler.keyboards.remind_2h_kb", return_value=MagicMock()):
            await sched.send_reminder_2h(mock_bot, booking)

    async def test_reminder_2h_generic_exception(self, mock_bot):
        import scheduler as sched
        booking = {"id": "bk8", "date": "2099-01-01", "time": "10:00",
                   "telegram_id": 111, "master": "A", "service": "Cut"}
        mock_bot.send_message = AsyncMock(side_effect=Exception("err"))
        with patch("scheduler.keyboards._format_date", return_value="01.01.2099"), \
             patch("scheduler.keyboards.remind_2h_kb", return_value=MagicMock()):
            await sched.send_reminder_2h(mock_bot, booking)

    async def test_review_request_generic_exception(self, mock_bot):
        import scheduler as sched
        booking = {"id": "bk9", "date": "2099-01-01", "time": "10:00",
                   "telegram_id": 111, "master": "A"}
        mock_bot.send_message = AsyncMock(side_effect=Exception("err"))
        with patch("scheduler.keyboards._format_date", return_value="01.01.2099"), \
             patch("scheduler.keyboards.review_kb", return_value=MagicMock()):
            await sched.send_review_request(mock_bot, booking)

    async def test_save_job_exception_handled(self):
        import scheduler as sched
        with patch("scheduler.storage.save_scheduler_job",
                   new_callable=AsyncMock, side_effect=Exception("db error")):
            await sched._save_job("job_id", "2099-01-01T10:00:00", "reminder_24h", "bk1")


class TestStartSchedulerRecovery:

    async def test_already_running_noop(self, mock_bot):
        import scheduler as sched
        mock_sched = MagicMock()
        mock_sched.running = True
        with patch("scheduler.scheduler", mock_sched):
            await sched.start_scheduler(mock_bot)
        mock_sched.start.assert_not_called()

    async def test_recovery_past_job_removed(self, mock_bot):
        import scheduler as sched
        import config
        tz = ZoneInfo(config.TIMEZONE)
        past_time = (datetime.now(tz) - timedelta(hours=1)).isoformat()
        jobs = [{"id": "old_job", "run_date": past_time,
                 "job_type": "reminder_24h", "booking_id": "bk_old"}]
        mock_sched = MagicMock()
        mock_sched.running = False
        remove_mock = AsyncMock()
        with patch("scheduler.scheduler", mock_sched), \
             patch("scheduler.storage.get_all_scheduler_jobs", new_callable=AsyncMock, return_value=jobs), \
             patch("scheduler.storage.remove_scheduler_job", remove_mock):
            await sched.start_scheduler(mock_bot)
        remove_mock.assert_called_with("old_job")

    async def test_recovery_inactive_booking_removed(self, mock_bot):
        import scheduler as sched
        import config
        tz = ZoneInfo(config.TIMEZONE)
        future_time = (datetime.now(tz) + timedelta(hours=24)).isoformat()
        jobs = [{"id": "job_inactive", "run_date": future_time,
                 "job_type": "reminder_24h", "booking_id": "bk_inactive"}]
        mock_sched = MagicMock()
        mock_sched.running = False
        remove_mock = AsyncMock()
        with patch("scheduler.scheduler", mock_sched), \
             patch("scheduler.storage.get_all_scheduler_jobs", new_callable=AsyncMock, return_value=jobs), \
             patch("scheduler.storage.get_booking_with_user", new_callable=AsyncMock, return_value=None), \
             patch("scheduler.storage.remove_scheduler_job", remove_mock):
            await sched.start_scheduler(mock_bot)
        remove_mock.assert_called_with("job_inactive")

    async def test_recovery_cancelled_booking_removed(self, mock_bot):
        import scheduler as sched
        import config
        tz = ZoneInfo(config.TIMEZONE)
        future_time = (datetime.now(tz) + timedelta(hours=24)).isoformat()
        jobs = [{"id": "job_cancel", "run_date": future_time,
                 "job_type": "reminder_24h", "booking_id": "bk_cancel"}]
        cancelled_booking = {"id": "bk_cancel", "status": "cancelled"}
        mock_sched = MagicMock()
        mock_sched.running = False
        remove_mock = AsyncMock()
        with patch("scheduler.scheduler", mock_sched), \
             patch("scheduler.storage.get_all_scheduler_jobs", new_callable=AsyncMock, return_value=jobs), \
             patch("scheduler.storage.get_booking_with_user",
                   new_callable=AsyncMock, return_value=cancelled_booking), \
             patch("scheduler.storage.remove_scheduler_job", remove_mock):
            await sched.start_scheduler(mock_bot)
        remove_mock.assert_called_with("job_cancel")

    async def test_recovery_all_four_job_types(self, mock_bot):
        import scheduler as sched
        import config
        tz = ZoneInfo(config.TIMEZONE)
        future_time = (datetime.now(tz) + timedelta(hours=24)).isoformat()
        active_booking = {"id": "bk_a", "date": "2099-01-01", "time": "10:00",
                          "name": "T", "telegram_id": 1, "master": "A",
                          "service": "S", "price": 100, "status": "active"}
        jobs = [
            {"id": "r24", "run_date": future_time, "job_type": "reminder_24h", "booking_id": "bk_a"},
            {"id": "r2h", "run_date": future_time, "job_type": "reminder_2h", "booking_id": "bk_a"},
            {"id": "ac", "run_date": future_time, "job_type": "auto_complete", "booking_id": "bk_a"},
            {"id": "rv", "run_date": future_time, "job_type": "review", "booking_id": "bk_a"},
        ]
        mock_sched = MagicMock()
        mock_sched.running = False
        with patch("scheduler.scheduler", mock_sched), \
             patch("scheduler.storage.get_all_scheduler_jobs",
                   new_callable=AsyncMock, return_value=jobs), \
             patch("scheduler.storage.get_booking_with_user",
                   new_callable=AsyncMock, return_value=active_booking), \
             patch("scheduler.storage.remove_scheduler_job", new_callable=AsyncMock):
            await sched.start_scheduler(mock_bot)
        assert mock_sched.add_job.call_count >= 4

    async def test_recovery_job_exception_skipped(self, mock_bot):
        import scheduler as sched
        import config
        tz = ZoneInfo(config.TIMEZONE)
        future_time = (datetime.now(tz) + timedelta(hours=24)).isoformat()
        jobs = [{"id": "bad_job", "run_date": future_time,
                 "job_type": "reminder_24h", "booking_id": "bk_bad"}]
        mock_sched = MagicMock()
        mock_sched.running = False
        with patch("scheduler.scheduler", mock_sched), \
             patch("scheduler.storage.get_all_scheduler_jobs",
                   new_callable=AsyncMock, return_value=jobs), \
             patch("scheduler.storage.get_booking_with_user",
                   new_callable=AsyncMock, side_effect=Exception("db error")), \
             patch("scheduler.storage.remove_scheduler_job", new_callable=AsyncMock):
            await sched.start_scheduler(mock_bot)

    async def test_recovery_db_exception_caught(self, mock_bot):
        import scheduler as sched
        mock_sched = MagicMock()
        mock_sched.running = False
        with patch("scheduler.scheduler", mock_sched), \
             patch("scheduler.storage.get_all_scheduler_jobs",
                   new_callable=AsyncMock, side_effect=Exception("DB down")):
            await sched.start_scheduler(mock_bot)

    async def test_recovery_naive_datetime_gets_tz(self, mock_bot):
        """Lines 231-233: naive datetime gets timezone attached"""
        import scheduler as sched
        import config
        tz = ZoneInfo(config.TIMEZONE)
        naive_future = (datetime.now(tz) + timedelta(hours=24)).replace(tzinfo=None).isoformat()
        active_booking = {"id": "bk_naive", "date": "2099-01-01", "time": "10:00",
                          "name": "T", "telegram_id": 1, "master": "A",
                          "service": "S", "price": 100, "status": "active"}
        jobs = [{"id": "naive_job", "run_date": naive_future,
                 "job_type": "reminder_24h", "booking_id": "bk_naive"}]
        mock_sched = MagicMock()
        mock_sched.running = False
        with patch("scheduler.scheduler", mock_sched), \
             patch("scheduler.storage.get_all_scheduler_jobs",
                   new_callable=AsyncMock, return_value=jobs), \
             patch("scheduler.storage.get_booking_with_user",
                   new_callable=AsyncMock, return_value=active_booking), \
             patch("scheduler.storage.remove_scheduler_job", new_callable=AsyncMock):
            await sched.start_scheduler(mock_bot)
        mock_sched.add_job.assert_called()


class TestShutdownScheduler:

    def test_shutdown_when_running(self):
        import scheduler as sched
        mock_sched = MagicMock()
        mock_sched.running = True
        with patch("scheduler.scheduler", mock_sched):
            sched.shutdown_scheduler()
        mock_sched.shutdown.assert_called_once_with(wait=False)

    def test_shutdown_when_not_running(self):
        import scheduler as sched
        mock_sched = MagicMock()
        mock_sched.running = False
        with patch("scheduler.scheduler", mock_sched):
            sched.shutdown_scheduler()
        mock_sched.shutdown.assert_not_called()

    def test_shutdown_exception_handled(self):
        import scheduler as sched
        mock_sched = MagicMock()
        mock_sched.running = True
        mock_sched.shutdown = MagicMock(side_effect=Exception("already stopped"))
        with patch("scheduler.scheduler", mock_sched):
            sched.shutdown_scheduler()


class TestCleanupJobs:

    async def test_cleanup_old_bookings_success(self):
        import scheduler as sched
        with patch("scheduler.storage.cleanup_old_bookings",
                   new_callable=AsyncMock, return_value=5):
            await sched.cleanup_old_bookings_job()

    async def test_cleanup_old_bookings_exception(self):
        import scheduler as sched
        with patch("scheduler.storage.cleanup_old_bookings",
                   new_callable=AsyncMock, side_effect=Exception("db error")):
            await sched.cleanup_old_bookings_job()

    async def test_cleanup_slot_locks_returns_nonzero(self):
        import scheduler as sched
        with patch("scheduler.storage.cleanup_expired_slot_locks",
                   new_callable=AsyncMock, return_value=3):
            await sched.cleanup_slot_locks_job()

    async def test_cleanup_slot_locks_returns_zero(self):
        import scheduler as sched
        with patch("scheduler.storage.cleanup_expired_slot_locks",
                   new_callable=AsyncMock, return_value=0):
            await sched.cleanup_slot_locks_job()

    async def test_cleanup_slot_locks_exception(self):
        import scheduler as sched
        with patch("scheduler.storage.cleanup_expired_slot_locks",
                   new_callable=AsyncMock, side_effect=Exception("err")):
            await sched.cleanup_slot_locks_job()
