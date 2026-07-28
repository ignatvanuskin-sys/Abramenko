import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import keyboards
import config


class TestMainMenuKeyboard:

    def test_main_menu_has_expected_buttons(self):
        kb = keyboards.main_menu_kb()
        texts = " ".join(b.text for row in kb.inline_keyboard for b in row)
        assert "Записаться" in texts
        assert "Мои записи" in texts
        assert "Услуги и цены" in texts
        assert "Портфолио" in texts
        assert "Контакты" in texts
        assert "О мастере" in texts
        # Should be compact: 2 buttons per row, not 6 in one column
        assert all(len(row) <= 2 for row in kb.inline_keyboard)


class TestServicesKeyboard:

    async def test_services_kb_is_compact(self):
        original = dict(config.SERVICES)
        config.SERVICES = {
            "Маникюр": 3000,
            "Педикюр": 4500,
            "Дизайн": 2000,
            "Наращивание": 8000,
        }
        try:
            kb = await keyboards.services_kb()
            # Each service row has up to 2 buttons
            assert all(len(row) <= 2 for row in kb.inline_keyboard[:-1])
            # Last row is back button
            assert any("Назад" in b.text for b in kb.inline_keyboard[-1])
        finally:
            config.SERVICES = original

    async def test_services_kb_callbacks_use_service_name(self):
        original = dict(config.SERVICES)
        config.SERVICES = {"Маникюр": 3000}
        try:
            kb = await keyboards.services_kb()
            service_button = next(
                b for row in kb.inline_keyboard for b in row
                if "Маникюр" in b.text
            )
            assert service_button.callback_data.startswith("service:Маникюр")
        finally:
            config.SERVICES = original


class TestDatesKeyboard:

    def test_dates_kb_is_compact(self):
        dates = ["2026-12-07", "2026-12-08", "2026-12-09"]
        kb = keyboards.dates_kb(dates)
        # Each row has up to 2 date buttons
        date_rows = [row for row in kb.inline_keyboard if len(row) <= 2]
        assert len(date_rows) >= 2
        assert any("Назад" in b.text for b in kb.inline_keyboard[-1])


class TestTimeSlotsKeyboard:

    def test_time_slots_kb_groups_by_four(self):
        slots = {f"{h:02d}:00": "free" for h in range(10, 14)}
        slots.update({f"{h:02d}:30": "free" for h in range(10, 14)})
        kb = keyboards.time_slots_kb(slots)
        # Time rows should have up to 4 buttons
        assert all(len(row) <= 4 for row in kb.inline_keyboard[:-1])

    def test_time_slots_kb_no_free_slots(self):
        slots = {"10:00": "busy", "10:30": "busy"}
        kb = keyboards.time_slots_kb(slots)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "Нет свободных слотов" in texts
        assert "Встать в лист ожидания" in texts


class TestBookingFlowWithoutMasterSelection:

    async def test_cb_book_skips_master_and_shows_services(self, db):
        from handlers.booking import cb_book
        import storage

        await storage.save_user(111, phone="+77001234567", username="u", first_name="Test")

        cb = MagicMock()
        cb.from_user.id = 111
        cb.from_user.first_name = "Test"
        cb.message = AsyncMock()
        cb.message.edit_text = AsyncMock()
        cb.message.answer = AsyncMock()
        cb.answer = AsyncMock()
        cb.bot = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={})

        with patch("handlers.booking.config.MAX_ACTIVE_BOOKINGS", 10), \
             patch("handlers.booking.config.RATE_LIMIT_WINDOW", 1800), \
             patch("handlers.booking.config.MAX_BOOKING_ATTEMPTS", 10):
            await cb_book(cb, state)

        # Should set state to choose_service and master is auto-selected
        state.set_state.assert_called()
        state.update_data.assert_called()
        args = state.update_data.call_args[1] or state.update_data.call_args[0][0]
        assert args.get("master") == config.MASTER_NAME
