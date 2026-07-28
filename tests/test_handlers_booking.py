
"""Tests for handlers/booking.py - FSM booking flow"""
import pytest
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from unittest.mock import AsyncMock, MagicMock, patch
from helpers import make_message, make_callback, make_fsm, SAMPLE_BOOKING


class TestSafeEdit:
    async def test_edits_successfully(self):
        from handlers.booking import _safe_edit
        msg = AsyncMock()
        await _safe_edit(msg, "hello")
        msg.edit_text.assert_called_once_with("hello", reply_markup=None, parse_mode="HTML")

    async def test_ignores_message_not_modified(self):
        from handlers.booking import _safe_edit
        msg = AsyncMock()
        msg.edit_text.side_effect = Exception("message is not modified")
        await _safe_edit(msg, "hello")  # must not raise

    async def test_fallback_answer_on_other_error(self):
        from handlers.booking import _safe_edit
        msg = AsyncMock()
        msg.edit_text.side_effect = Exception("message to edit not found")
        await _safe_edit(msg, "hello")
        msg.answer.assert_called_once_with("hello", reply_markup=None, parse_mode="HTML")

    async def test_passes_reply_markup(self):
        from handlers.booking import _safe_edit
        msg = AsyncMock()
        kb = MagicMock()
        await _safe_edit(msg, "text", reply_markup=kb)
        msg.edit_text.assert_called_once_with("text", reply_markup=kb, parse_mode="HTML")


class TestGenerateTimeSlots:
    def test_generates_slots_for_weekday(self):
        from handlers.booking import _generate_time_slots
        slots = _generate_time_slots("2026-12-07")  # Monday
        assert "10:00" in slots
        assert "20:30" in slots

    def test_no_closing_time_slot(self):
        """Slot at exactly closing time must not be generated."""
        from handlers.booking import _generate_time_slots
        slots = _generate_time_slots("2026-12-07")
        assert "21:00" not in slots

    def test_sunday_has_shorter_hours(self):
        from handlers.booking import _generate_time_slots
        import config
        from unittest.mock import patch
        canonical = {"monday": (10, 21), "tuesday": (10, 21), "wednesday": (10, 21),
                     "thursday": (10, 21), "friday": (10, 21), "saturday": (10, 21),
                     "sunday": (11, 19)}
        with patch.object(config, "WORKING_HOURS", canonical):
            slots_sun = _generate_time_slots("2026-12-06")  # Sunday
            slots_mon = _generate_time_slots("2026-12-07")  # Monday
        assert len(slots_sun) < len(slots_mon)

    def test_invalid_date_falls_back(self):
        from handlers.booking import _generate_time_slots
        slots = _generate_time_slots("not-a-date")
        assert len(slots) > 0


class TestGetAvailableSlots:
    async def test_all_free_on_empty_db(self, db):
        from handlers.booking import _get_available_slots
        slots = await _get_available_slots("2026-12-07", "Alibek")
        assert all(v == "free" for v in slots.values())

    async def test_booked_slot_marked_busy(self, db):
        import storage
        from handlers.booking import _get_available_slots
        await storage.save_booking({
            "date": "2026-12-07", "time": "10:00", "name": "Ivan",
            "telegram_id": 111, "username": "u", "master": "Alibek",
            "service": "Haircut", "price": 3000
        })
        slots = await _get_available_slots("2026-12-07", "Alibek")
        assert slots.get("10:00") == "busy"

    async def test_locked_slot_marked_busy(self, db):
        import storage
        from handlers.booking import _get_available_slots
        await storage.create_slot_lock("2026-12-07", "10:30", "Alibek")
        slots = await _get_available_slots("2026-12-07", "Alibek")
        assert slots.get("10:30") == "busy"


class TestFinalizeBooking:
    async def test_sends_confirmation(self):
        from handlers.booking import _finalize_booking
        booking = {**SAMPLE_BOOKING}
        bot = AsyncMock()
        send_fn = AsyncMock()
        with patch("handlers.booking.scheduler.schedule_reminders", new=AsyncMock()), \
             patch("handlers.booking.config.ADMIN_IDS", []):
            await _finalize_booking(booking, "bid123", send_fn, bot)
        send_fn.assert_called_once()

    async def test_notifies_admins(self):
        from handlers.booking import _finalize_booking
        bot = AsyncMock()
        send_fn = AsyncMock()
        with patch("handlers.booking.scheduler.schedule_reminders", new=AsyncMock()), \
             patch("handlers.booking.config.ADMIN_IDS", [999]):
            await _finalize_booking(SAMPLE_BOOKING, "bid123", send_fn, bot)
        bot.send_message.assert_called()

    async def test_schedules_reminders(self):
        from handlers.booking import _finalize_booking
        bot = AsyncMock()
        send_fn = AsyncMock()
        with patch("handlers.booking.scheduler.schedule_reminders", new=AsyncMock()) as mock_sched, \
             patch("handlers.booking.config.ADMIN_IDS", []):
            await _finalize_booking(SAMPLE_BOOKING, "bid123", send_fn, bot)
            mock_sched.assert_called_once()


class TestHandleEnterName:
    async def test_valid_name_creates_booking(self, db):
        from handlers.booking import handle_enter_name
        import storage
        msg = make_message(text="Ivan")
        fsm = make_fsm(data={
            "date": "2026-12-07", "time": "10:00",
            "master": "Alibek", "service": "Haircut", "price": 3000
        })
        with patch("handlers.booking.scheduler.schedule_reminders", new=AsyncMock()), \
             patch("handlers.booking.config.ADMIN_IDS", []):
            await handle_enter_name(msg, fsm)
        msg.answer.assert_called()
        fsm.clear.assert_called_once()

    async def test_empty_name_rejected(self):
        from handlers.booking import handle_enter_name
        msg = make_message(text="   ")
        fsm = make_fsm()
        await handle_enter_name(msg, fsm)
        msg.answer.assert_called()
        fsm.clear.assert_not_called()

    async def test_name_with_digits_rejected(self):
        from handlers.booking import handle_enter_name
        msg = make_message(text="Ivan123")
        fsm = make_fsm()
        await handle_enter_name(msg, fsm)
        msg.answer.assert_called()
        fsm.clear.assert_not_called()

    async def test_name_too_long_rejected(self):
        from handlers.booking import handle_enter_name
        msg = make_message(text="A" * 51)
        fsm = make_fsm()
        await handle_enter_name(msg, fsm)
        msg.answer.assert_called()

    async def test_name_only_hyphens_rejected(self):
        from handlers.booking import handle_enter_name
        msg = make_message(text="---")
        fsm = make_fsm()
        await handle_enter_name(msg, fsm)
        fsm.clear.assert_not_called()

    async def test_slot_busy_shows_error(self, db):
        from handlers.booking import handle_enter_name
        import storage
        # Book the slot first
        await storage.save_booking({
            "date": "2026-12-07", "time": "10:00", "name": "Ivan",
            "telegram_id": 222, "username": "u", "master": "Alibek",
            "service": "Haircut", "price": 3000
        })
        msg = make_message(text="Peter")
        fsm = make_fsm(data={
            "date": "2026-12-07", "time": "10:00",
            "master": "Alibek", "service": "Haircut", "price": 3000
        })
        await handle_enter_name(msg, fsm)
        msg.answer.assert_called()


class TestCbCancelBooking:
    async def test_clears_fsm_and_shows_cancelled(self):
        from handlers.booking import cb_cancel_booking
        cb = make_callback(data="cancel_booking")
        fsm = make_fsm(data={})
        await cb_cancel_booking(cb, fsm)
        fsm.clear.assert_called_once()
        cb.message.edit_text.assert_called_once()

    async def test_releases_slot_lock_if_set(self, db):
        import storage
        from handlers.booking import cb_cancel_booking
        await storage.create_slot_lock("2026-12-07", "10:00", "Alibek")
        cb = make_callback(data="cancel_booking")
        fsm = make_fsm(data={
            "date": "2026-12-07", "time": "10:00", "master": "Alibek"
        })
        await cb_cancel_booking(cb, fsm)
        import aiosqlite, config
        async with aiosqlite.connect(config.DB_PATH) as conn:
            c = await conn.execute("SELECT COUNT(*) FROM slot_locks")
            assert (await c.fetchone())[0] == 0


class TestCbRemindCancel:
    async def test_cancels_valid_booking(self, db):
        from handlers.booking import cb_remind_cancel
        import storage
        bid = await storage.save_booking(SAMPLE_BOOKING)
        cb = make_callback(data=f"remind_cancel:{bid}")
        bot = AsyncMock()
        with patch("handlers.booking.scheduler.cancel_reminders", new=AsyncMock()):
            await cb_remind_cancel(cb, bot)
        cb.message.edit_text.assert_called_once()

    async def test_wrong_user_cannot_cancel(self, db):
        from handlers.booking import cb_remind_cancel
        import storage
        bid = await storage.save_booking(SAMPLE_BOOKING)
        cb = make_callback(data=f"remind_cancel:{bid}", user_id=999)
        bot = AsyncMock()
        with patch("handlers.booking.scheduler.cancel_reminders", new=AsyncMock()):
            await cb_remind_cancel(cb, bot)
        # Booking stays active
        bookings = await storage.get_user_bookings(111)
        assert any(b["id"] == bid for b in bookings)


class TestCbNoSlots:
    async def test_shows_alert(self):
        from handlers.booking import cb_no_slots
        cb = make_callback(data="no_slots")
        await cb_no_slots(cb)
        cb.answer.assert_called_once()
        call_kwargs = cb.answer.call_args[1]
        assert call_kwargs.get("show_alert") is True


class TestCbConfirmDeprecated:
    async def test_active_fsm_warns_user(self):
        from handlers.booking import cb_confirm_deprecated
        from handlers.booking import BookingStates
        cb = make_callback(data="confirm")
        fsm = make_fsm(state=str(BookingStates.enter_name))
        await cb_confirm_deprecated(cb, fsm)
        cb.answer.assert_called()
        fsm.clear.assert_not_called()

    async def test_no_fsm_shows_stale_message(self):
        from handlers.booking import cb_confirm_deprecated
        cb = make_callback(data="confirm")
        fsm = make_fsm(state=None)
        await cb_confirm_deprecated(cb, fsm)
        fsm.clear.assert_called_once()

