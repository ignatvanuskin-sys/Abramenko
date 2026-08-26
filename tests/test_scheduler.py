# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com

"""Tests for scheduler.py"""
import pytest
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter


SAMPLE = {
    "id": "bid123",
    "date": "2026-12-01",
    "time": "10:00",
    "master": "Alibek",
    "service": "Haircut",
    "price": 3000,
    "name": "Ivan",
    "telegram_id": 111,
}


class TestSendReminder24h:
    async def test_sends_message_on_success(self):
        import scheduler
        bot = AsyncMock()
        await scheduler.send_reminder_24h(bot, SAMPLE)
        bot.send_message.assert_called_once()
        args = bot.send_message.call_args[0]
        assert args[0] == SAMPLE["telegram_id"]

    async def test_handles_forbidden(self):
        import scheduler
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramForbiddenError(
            method=MagicMock(), message="Forbidden"
        )
        with patch.object(scheduler, "cancel_reminders", new=AsyncMock()) as mock_cancel:
            await scheduler.send_reminder_24h(bot, SAMPLE)
            mock_cancel.assert_called_once_with(SAMPLE["id"])

    async def test_handles_retry_after(self):
        """TelegramRetryAfter should be caught without crashing."""
        import scheduler
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramRetryAfter(
            method=MagicMock(), message="Too Many Requests", retry_after=5
        )
        # Should not raise
        await scheduler.send_reminder_24h(bot, SAMPLE)

    async def test_handles_generic_error(self):
        import scheduler
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("Network error")
        await scheduler.send_reminder_24h(bot, SAMPLE)


class TestSendReminder2h:
    async def test_sends_message(self):
        import scheduler
        bot = AsyncMock()
        await scheduler.send_reminder_2h(bot, SAMPLE)
        bot.send_message.assert_called_once()

    async def test_handles_forbidden(self):
        import scheduler
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramForbiddenError(
            method=MagicMock(), message="Forbidden"
        )
        with patch.object(scheduler, "cancel_reminders", new=AsyncMock()) as mock_cancel:
            await scheduler.send_reminder_2h(bot, SAMPLE)
            mock_cancel.assert_called_once_with(SAMPLE["id"])


class TestSendReviewRequest:
    async def test_sends_message(self):
        import scheduler
        bot = AsyncMock()
        await scheduler.send_review_request(bot, SAMPLE)
        bot.send_message.assert_called_once()

    async def test_handles_forbidden(self):
        import scheduler
        bot = AsyncMock()
        bot.send_message.side_effect = TelegramForbiddenError(
            method=MagicMock(), message="Forbidden"
        )
        await scheduler.send_review_request(bot, SAMPLE)


class TestCleanupSlotLocksJob:
    async def test_calls_storage(self, db):
        import scheduler, storage
        # Add an expired lock
        from datetime import datetime, timedelta
        import aiosqlite, config
        past = (datetime.now() - timedelta(minutes=10)).isoformat()
        async with aiosqlite.connect(config.DB_PATH) as conn:
            await conn.execute(
                "INSERT INTO slot_locks (date, time, master, master_key, owner_id, owner_token, locked_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("2026-12-01", "10:00", "default", "default", None, "", past, past)
            )
            await conn.commit()
        await scheduler.cleanup_slot_locks_job()
        # No exception = pass


class TestCancelReminders:
    async def test_removes_scheduler_jobs(self):
        import scheduler
        mock_scheduler = MagicMock()
        mock_scheduler.remove_job = MagicMock()
        scheduler.scheduler = mock_scheduler
        mock_scheduler.remove_job.side_effect = Exception("not found")
        with patch("scheduler.storage") as mock_storage:
            mock_storage.remove_scheduler_job = AsyncMock()
            await scheduler.cancel_reminders("bid123")  # must not raise


class TestAutoCompleteBooking:
    async def test_completes_and_notifies_admin(self, db):
        import scheduler, storage, config
        config.ADMIN_IDS = [999]
        config.LOYALTY_VISIT_INTERVAL = 5
        bid = await storage.save_booking(
            {"date": "2026-12-01", "time": "10:00", "name": "Ivan",
             "telegram_id": 111, "username": "u", "master": "Alibek",
             "service": "Haircut", "price": 3000}
        )
        bot = AsyncMock()
        booking = {**SAMPLE, "id": bid}
        await scheduler.auto_complete_booking(bot, booking)
        bot.send_message.assert_called()

    async def test_handles_already_completed(self, db):
        import scheduler
        bot = AsyncMock()
        await scheduler.auto_complete_booking(bot, {**SAMPLE, "id": "nonexistent"})
        # No exception = pass
