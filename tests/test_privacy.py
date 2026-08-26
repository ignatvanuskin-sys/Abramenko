# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
from unittest.mock import AsyncMock, patch

from helpers import make_message


async def _insert_old_completed_booking(storage, dbmod, telegram_id=777):
    async with dbmod.acquire() as conn:
        await conn.execute(
            "INSERT INTO bookings "
            "(id, date, time, name, telegram_id, username, master, service, price, duration_minutes, comment, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            "old-booking",
            "2020-01-01",
            "10:00",
            "Private Name",
            telegram_id,
            "private_username",
            "Anna",
            "Service",
            5000,
            60,
            "private comment",
            "completed",
            "2020-01-01T10:00:00+06:00",
        )
        await conn.execute(
            "INSERT INTO admin_audit_log (admin_id, action, entity_type, entity_id, old_value, new_value, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            1,
            "old_action",
            "booking",
            "old-booking",
            "old",
            "new",
            "2020-01-01T10:00:00+06:00",
        )
        await conn.commit()


async def test_export_client_data_collects_related_rows(db):
    import storage

    await storage.save_user(111, phone="+77770000000", username="client", first_name="Client")
    booking_id = await storage.save_booking(
        {
            "date": "2026-12-07",
            "time": "10:00",
            "name": "Client",
            "telegram_id": 111,
            "username": "client",
            "master": "Anna",
            "service": "Service",
            "price": 3000,
        }
    )
    await storage.save_booking(
        {
            "date": "2026-12-08",
            "time": "11:00",
            "name": "Other Client",
            "telegram_id": 112,
            "username": "other",
            "master": "Anna",
            "service": "Service",
            "price": 3000,
        }
    )
    await storage.add_to_waitlist(111, "Client", "Anna", "Service", "2026-12-08", "11:00")
    await storage.update_loyalty(111, "Client")
    await storage.complete_booking(booking_id)
    assert await storage.save_review(booking_id, 111, 5, "good") is True

    data = await storage.export_client_data(111)

    assert data["user"]["phone"] == "+77770000000"
    assert len(data["bookings"]) == 1
    assert len(data["waitlist"]) == 1
    assert data["loyalty"]["visits"] == 1
    assert len(data["reviews"]) == 1


async def test_anonymize_client_data_preserves_booking_without_pii(db):
    import storage

    await storage.save_user(222, phone="+77771112233", username="secret", first_name="Secret")
    booking_id = await storage.save_booking(
        {
            "date": "2026-12-07",
            "time": "12:00",
            "name": "Secret Name",
            "telegram_id": 222,
            "username": "secret",
            "master": "Anna",
            "service": "Service",
            "price": 4000,
            "comment": "PII comment",
        }
    )

    counts = await storage.anonymize_client_data(222)
    booking = await storage.get_booking_with_user(booking_id)
    exported_after = await storage.export_client_data(222)

    assert counts["users_deleted"] == 1
    assert counts["bookings_anonymized"] == 1
    assert booking["telegram_id"] == 0
    assert booking["name"] == "Удаленный клиент"
    assert booking["username"] == ""
    assert booking["comment"] == ""
    assert booking["price"] == 4000
    assert exported_after["user"] is None
    assert exported_after["bookings"] == []


async def test_retention_anonymizes_old_completed_bookings_and_audit(db):
    import storage
    import db as dbmod

    await _insert_old_completed_booking(storage, dbmod)

    result = await storage.apply_retention_policy(booking_days=30, audit_days=30)
    row = await storage.get_booking_with_user("old-booking")
    audit_rows = await storage.get_admin_audit_log(limit=10)

    assert result["bookings_anonymized"] == 1
    assert result["admin_audit_deleted"] == 1
    assert row["telegram_id"] == 0
    assert row["name"] == "Удаленный клиент"
    assert row["price"] == 5000
    assert audit_rows == []


async def test_export_bookings_csv_excludes_pii(db):
    import storage

    await storage.save_booking(
        {
            "date": "2026-12-07",
            "time": "13:00",
            "name": "PII Name",
            "telegram_id": 333,
            "username": "pii_user",
            "master": "Anna",
            "service": "Service",
            "price": 4000,
            "comment": "secret comment",
        }
    )

    rows = await storage.export_bookings_csv()

    assert rows
    assert "telegram_id" not in rows[0]
    assert "username" not in rows[0]
    assert "name" not in rows[0]
    assert "comment" not in rows[0]
    assert rows[0]["price"] == 4000


async def test_admin_privacy_delete_command_calls_storage(monkeypatch):
    import config
    from handlers import admin

    monkeypatch.setattr(config, "ADMIN_IDS", [999])
    message = make_message("/privacy_delete 111", user_id=999, chat_id=999)
    anonymize_mock = AsyncMock(return_value={"bookings_anonymized": 1})

    with (
        patch.object(admin.storage, "anonymize_client_data", anonymize_mock),
        patch.object(admin, "_audit", AsyncMock()),
    ):
        await admin.cmd_privacy_delete(message)

    anonymize_mock.assert_awaited_once_with(111)
    message.bot.send_message.assert_awaited()


async def test_admin_privacy_export_command_sends_document(monkeypatch):
    import config
    from handlers import admin

    monkeypatch.setattr(config, "ADMIN_IDS", [999])
    message = make_message("/privacy_export 111", user_id=999, chat_id=999)
    message.answer_document = AsyncMock()
    export_mock = AsyncMock(return_value={"telegram_id": 111, "bookings": []})

    with (
        patch.object(admin.storage, "export_client_data", export_mock),
        patch.object(admin, "_audit", AsyncMock()),
    ):
        await admin.cmd_privacy_export(message)

    export_mock.assert_awaited_once_with(111)
    message.answer_document.assert_awaited_once()
