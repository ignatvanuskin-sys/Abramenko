import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


def _booking(**overrides):
    data = {
        "date": "2026-12-07",
        "time": "10:00",
        "name": "Client",
        "telegram_id": 100,
        "username": "client",
        "master": "Anna",
        "service": "Маникюр",
        "price": 3000,
        "duration_minutes": 60,
    }
    data.update(overrides)
    return data


class TestDurationAwareBookingStorage:

    async def test_duration_blocks_overlapping_start_times(self, db):
        import storage

        first_id = await storage.save_booking(_booking(time="10:00", duration_minutes=60))
        overlapping_id = await storage.save_booking(
            _booking(time="10:30", telegram_id=101, duration_minutes=30)
        )
        adjacent_id = await storage.save_booking(
            _booking(time="11:00", telegram_id=102, duration_minutes=30)
        )

        assert first_id
        assert overlapping_id is None
        assert adjacent_id

        booked = await storage.get_booked_slots("2026-12-07", "Anna")
        durations = {row["time"]: row["duration_minutes"] for row in booked}
        assert durations["10:00"] == 60
        assert durations["11:00"] == 30

    async def test_cancelled_duration_range_becomes_available(self, db):
        import storage

        booking_id = await storage.save_booking(_booking(time="10:00", duration_minutes=60))
        cancelled = await storage.cancel_booking(booking_id, telegram_id=100)
        replacement_id = await storage.save_booking(
            _booking(time="10:30", telegram_id=101, duration_minutes=30)
        )

        assert cancelled["id"] == booking_id
        assert replacement_id

    async def test_range_unavailable_blocks_partial_overlap(self, db):
        import storage

        await storage.add_unavailable_period(
            "2026-12-07", start_time="10:30", end_time="11:30", master="Anna", reason="break"
        )

        overlapping_id = await storage.save_booking(_booking(time="10:00", duration_minutes=60))
        adjacent_id = await storage.save_booking(
            _booking(time="11:30", telegram_id=101, duration_minutes=30)
        )

        assert overlapping_id is None
        assert adjacent_id

    async def test_full_day_unavailable_blocks_new_bookings_without_cancelling_existing(self, db):
        import storage

        existing_id = await storage.save_booking(_booking(time="10:00", duration_minutes=30))
        period_id = await storage.add_unavailable_period("2026-12-07", master="Anna", reason="day off")
        new_id = await storage.save_booking(_booking(time="12:00", telegram_id=101, duration_minutes=30))
        user_bookings = await storage.get_user_bookings(100)

        assert existing_id
        assert period_id
        assert new_id is None
        assert any(row["id"] == existing_id and row["status"] == "active" for row in user_bookings)

    async def test_waitlist_open_period_respects_duration(self, db):
        import storage

        booking_id = await storage.save_booking(_booking(time="10:00", duration_minutes=60))
        await storage.add_to_waitlist(
            201, "Waiter", "Anna", "Маникюр", "2026-12-07", "10:30", duration_minutes=30
        )
        await storage.add_to_waitlist(
            202, "Later", "Anna", "Маникюр", "2026-12-07", "11:00", duration_minutes=30
        )

        await storage.cancel_booking(booking_id, telegram_id=100)
        waitlist = await storage.get_waitlist_for_open_period("2026-12-07", "Anna", "10:00", 60)

        assert [row["telegram_id"] for row in waitlist] == [201]


class TestDurationAwareAvailability:

    async def test_available_slots_require_consecutive_free_slices(self, db):
        import storage
        from handlers.booking import _get_available_slots

        await storage.save_booking(_booking(time="10:00", duration_minutes=60))

        slots = await _get_available_slots("2026-12-07", "Anna", duration_minutes=60)

        assert slots["10:00"] == "busy"
        assert slots["10:30"] == "busy"
        assert slots["11:00"] == "free"
        assert "20:30" not in slots

    async def test_unavailable_slot_marked_busy(self, db):
        import storage
        from handlers.booking import _get_available_slots

        await storage.add_unavailable_slot("2026-12-07", "10:00", master="Anna", reason="blocked")

        slots = await _get_available_slots("2026-12-07", "Anna", duration_minutes=30)

        assert slots["10:00"] == "busy"
        assert slots["10:30"] == "free"
