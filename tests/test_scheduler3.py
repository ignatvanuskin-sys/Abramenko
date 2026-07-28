import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from tests.helpers import SAMPLE_BOOKING


class TestSchedulerFunctions:

    async def test_auto_complete_booking_not_found(self, db):
        import scheduler, storage
        await storage.init_db()
        bot = AsyncMock()
        booking = {"id": "nonexistent", "telegram_id": 111}
        with patch("storage.complete_booking", AsyncMock(return_value=None)):
            await scheduler.auto_complete_booking(bot, booking)

    async def test_auto_complete_booking_exception(self):
        import scheduler
        bot = AsyncMock()
        booking = {"id": "bid", "telegram_id": 111}
        with patch("storage.complete_booking", side_effect=Exception("fail")):
            await scheduler.auto_complete_booking(bot, booking)

    async def test_send_reminder_24h_success(self):
        import scheduler
        bot = AsyncMock()
        booking = {"id": "bid", "telegram_id": 111, "date": "2026-12-10",
                   "time": "10:00", "master": "Alibek", "service": "Haircut"}
        await scheduler.send_reminder_24h(bot, booking)
        bot.send_message.assert_called()

    async def test_send_reminder_24h_forbidden(self):
        import scheduler
        from aiogram.exceptions import TelegramForbiddenError
        bot = AsyncMock()
        booking = {"id": "bid", "telegram_id": 111, "date": "2026-12-10",
                   "time": "10:00", "master": "Alibek", "service": "Haircut"}
        bot.send_message.side_effect = TelegramForbiddenError(method=MagicMock(), message="Forbidden")
        with patch("scheduler.cancel_reminders", AsyncMock()):
            await scheduler.send_reminder_24h(bot, booking)

    async def test_send_reminder_2h_success(self):
        import scheduler
        bot = AsyncMock()
        booking = {"id": "bid", "telegram_id": 111, "date": "2026-12-10",
                   "time": "10:00", "master": "Alibek", "service": "Haircut"}
        await scheduler.send_reminder_2h(bot, booking)
        bot.send_message.assert_called()

    async def test_send_reminder_2h_forbidden(self):
        import scheduler
        from aiogram.exceptions import TelegramForbiddenError
        bot = AsyncMock()
        booking = {"id": "bid", "telegram_id": 111, "date": "2026-12-10",
                   "time": "10:00", "master": "Alibek", "service": "Haircut"}
        bot.send_message.side_effect = TelegramForbiddenError(method=MagicMock(), message="Forbidden")
        with patch("scheduler.cancel_reminders", AsyncMock()):
            await scheduler.send_reminder_2h(bot, booking)

    async def test_send_review_request_success(self):
        import scheduler
        bot = AsyncMock()
        booking = {"id": "bid", "telegram_id": 111, "date": "2026-12-10",
                   "time": "10:00", "master": "Alibek", "service": "Haircut"}
        await scheduler.send_review_request(bot, booking)
        bot.send_message.assert_called()

    async def test_send_review_request_forbidden(self):
        import scheduler
        from aiogram.exceptions import TelegramForbiddenError
        bot = AsyncMock()
        booking = {"id": "bid", "telegram_id": 111, "date": "2026-12-10",
                   "time": "10:00", "master": "Alibek", "service": "Haircut"}
        bot.send_message.side_effect = TelegramForbiddenError(method=MagicMock(), message="Forbidden")
        await scheduler.send_review_request(bot, booking)

    async def test_cleanup_old_bookings_job(self, db):
        import scheduler, storage
        await storage.init_db()
        with patch("storage.cleanup_old_bookings", AsyncMock(return_value=5)):
            await scheduler.cleanup_old_bookings_job()

    async def test_cleanup_old_bookings_exception(self):
        import scheduler
        with patch("storage.cleanup_old_bookings", side_effect=Exception("fail")):
            await scheduler.cleanup_old_bookings_job()

    async def test_cleanup_slot_locks_job(self, db):
        import scheduler, storage
        await storage.init_db()
        with patch("storage.cleanup_expired_slot_locks", AsyncMock(return_value=3)):
            await scheduler.cleanup_slot_locks_job()

    async def test_cleanup_slot_locks_exception(self):
        import scheduler
        with patch("storage.cleanup_expired_slot_locks", side_effect=Exception("fail")):
            await scheduler.cleanup_slot_locks_job()

    async def test_schedule_reminders_past_booking(self, db):
        import scheduler, storage
        await storage.init_db()
        bot = AsyncMock()
        booking = {"id": "some_bid", "date": "2020-01-01", "time": "10:00",
                   "telegram_id": 111, "name": "Test"}
        with patch("storage.save_scheduler_job", AsyncMock()):
            await scheduler.schedule_reminders(bot, booking)

    async def test_cancel_reminders(self, db):
        import scheduler, storage
        await storage.init_db()
        with patch("storage.remove_scheduler_job", AsyncMock()):
            await scheduler.cancel_reminders("test_bid")

    def test_shutdown_scheduler_not_running(self):
        import scheduler
        if scheduler.scheduler.running:
            scheduler.scheduler.shutdown(wait=False)
        scheduler.shutdown_scheduler()
