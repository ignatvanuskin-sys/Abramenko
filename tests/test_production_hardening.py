# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import asyncio
import gzip
import os
import sqlite3
import subprocess
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytz

from helpers import make_callback, make_fsm


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


async def test_concurrent_overlapping_multislot_booking_sqlite(db):
    import db as dbmod
    import storage

    first = _booking(time="10:00", telegram_id=501, duration_minutes=60)
    second = _booking(time="10:30", telegram_id=502, duration_minutes=60)

    results = await asyncio.gather(storage.save_booking(first), storage.save_booking(second))

    assert sum(1 for result in results if result) == 1
    assert sum(1 for result in results if result is None) == 1
    async with dbmod.acquire() as conn:
        slot_count = await conn.fetchval("SELECT COUNT(*) FROM booking_slots")
    assert slot_count == 2


async def test_forged_stale_time_callback_returns_before_lock(monkeypatch):
    from handlers import booking

    cb = make_callback(data="time:10:00", user_id=777)
    state = make_fsm(data={"date": "2026-12-07", "duration_minutes": 30})
    create_lock = AsyncMock()

    monkeypatch.setattr(booking, "_get_available_slots", AsyncMock(return_value={}))
    monkeypatch.setattr(booking, "_generate_time_slots", lambda date: ["10:00"])
    monkeypatch.setattr(booking.storage, "create_slot_lock", create_lock)

    await booking.cb_choose_time(cb, state)

    create_lock.assert_not_awaited()
    state.set_state.assert_not_called()
    cb.answer.assert_awaited_once()


async def test_save_booking_rejects_out_of_hours_and_unavailable(db):
    import storage

    out_of_hours = await storage.save_booking(_booking(time="20:30", duration_minutes=60))
    await storage.add_unavailable_period("2026-12-07", "12:00", "13:00", master="Anna")
    unavailable = await storage.save_booking(_booking(time="12:00", telegram_id=101, duration_minutes=30))

    assert out_of_hours is None
    assert unavailable is None


async def test_save_booking_rejects_too_soon(db, monkeypatch):
    import config
    import storage

    tz = pytz.timezone(config.TIMEZONE)
    fixed_now = tz.localize(datetime(2026, 12, 7, 9, 30))
    monkeypatch.setattr(storage, "get_now", lambda timezone: fixed_now)
    monkeypatch.setattr(config, "MIN_BOOKING_ADVANCE_MINUTES", 60)

    result = await storage.save_booking(_booking(time="10:00", duration_minutes=30))

    assert result is None


async def test_renaming_master_does_not_open_busy_slot(db, monkeypatch):
    import config
    import storage

    monkeypatch.setattr(config, "MASTER_NAME", "Anna")
    first = await storage.save_booking(_booking(master=config.MASTER_NAME, time="10:00", duration_minutes=60))
    monkeypatch.setattr(config, "MASTER_NAME", "Maria")
    second = await storage.save_booking(
        _booking(master=config.MASTER_NAME, telegram_id=102, time="10:30", duration_minutes=30)
    )

    assert first
    assert second is None


async def test_atomic_bonus_spend_under_concurrency(db):
    import storage

    await storage.update_loyalty(700, "Bonus Client")
    assert await storage.add_bonus(700, 1000) is True

    first = _booking(telegram_id=700, time="10:00", duration_minutes=30, price=3000, apply_discounts=True)
    second = _booking(telegram_id=700, time="11:00", duration_minutes=30, price=3000, apply_discounts=True)

    results = await asyncio.gather(storage.save_booking(first), storage.save_booking(second))
    loyalty = await storage.get_loyalty(700)

    assert all(results)
    assert sorted([first.get("bonus_spent", 0), second.get("bonus_spent", 0)]) == [0, 1000]
    assert loyalty["bonuses"] == 0


async def test_save_booking_enforces_active_limit_under_concurrency(db, monkeypatch):
    import config
    import storage

    monkeypatch.setattr(config, "MAX_ACTIVE_BOOKINGS", 1)

    results = await asyncio.gather(
        storage.save_booking(_booking(telegram_id=710, time="10:00", duration_minutes=30)),
        storage.save_booking(_booking(telegram_id=710, time="11:00", duration_minutes=30)),
    )

    assert sum(1 for result in results if result) == 1
    assert sum(1 for result in results if result is None) == 1


async def test_user_cannot_release_another_users_slot_lock(db):
    import storage

    assert await storage.create_slot_lock(
        "2026-12-07", "10:00", "Anna", owner_id=1, owner_token="owner-a"
    ) is True

    await storage.release_slot_lock(
        "2026-12-07", "10:00", "Anna", owner_id=2, owner_token="owner-b"
    )
    assert "10:00" in await storage.get_locked_slots("2026-12-07", "Anna")

    await storage.release_slot_lock(
        "2026-12-07", "10:00", "Anna", owner_id=1, owner_token="owner-a"
    )
    assert "10:00" not in await storage.get_locked_slots("2026-12-07", "Anna")


async def test_waitlist_rejects_free_slot_and_dedupes_race(db):
    import storage

    assert await storage.add_to_waitlist(
        801, "Waiter", "Anna", "Маникюр", "2026-12-07", "10:00", duration_minutes=30
    ) is False

    assert await storage.save_booking(_booking(time="10:00", telegram_id=802, duration_minutes=30))
    results = await asyncio.gather(
        storage.add_to_waitlist(803, "Waiter", "Anna", "Маникюр", "2026-12-07", "10:00", duration_minutes=30),
        storage.add_to_waitlist(803, "Waiter", "Anna", "Маникюр", "2026-12-07", "10:00", duration_minutes=30),
    )

    assert sorted(results) == [False, True]
    rows = await storage.get_waitlist_for_slot("2026-12-07", "10:00", "Anna")
    assert [row["telegram_id"] for row in rows] == [803]


async def test_forged_stale_waitlist_callback_returns_before_insert(monkeypatch):
    from handlers import booking

    cb = make_callback(data="waitlist:10:00", user_id=804)
    state = make_fsm(data={"date": "2026-12-07", "service": "Маникюр", "duration_minutes": 30})
    add_to_waitlist = AsyncMock()

    monkeypatch.setattr(booking, "_get_available_slots", AsyncMock(return_value={"10:00": "free"}))
    monkeypatch.setattr(booking.storage, "add_to_waitlist", add_to_waitlist)

    await booking.cb_waitlist(cb, state)

    add_to_waitlist.assert_not_awaited()
    cb.answer.assert_awaited_once()


async def test_retention_scheduler_uses_config(monkeypatch):
    import config
    import scheduler

    cleanup = AsyncMock(return_value=3)
    monkeypatch.setattr(config, "PRIVACY_RETENTION_DAYS", 456)
    monkeypatch.setattr(scheduler.storage, "cleanup_old_bookings", cleanup)

    await scheduler.cleanup_old_bookings_job()

    cleanup.assert_awaited_once_with(days=456)


async def test_sqlite_backup_restore_preserves_integrity_tables(db, tmp_path, monkeypatch):
    import backup
    import config
    import storage

    await storage.save_booking(_booking(time="10:00", duration_minutes=60))
    await storage.add_unavailable_period("2026-12-07", "13:00", "14:00", master="Anna")
    monkeypatch.setattr(backup, "_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(backup.os, "getenv", lambda name, default="": "")

    backup_file = backup.backup_database()
    assert backup_file is not None
    assert backup.restore_check(backup_file) is True

    restored = tmp_path / "restored.db"
    with gzip.open(backup_file, "rb") as f_in, open(restored, "wb") as f_out:
        f_out.write(f_in.read())
    conn = sqlite3.connect(restored)
    try:
        assert conn.execute("SELECT COUNT(*) FROM unavailable_periods").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM booking_slots").fetchone()[0] == 2
    finally:
        conn.close()


async def test_sqlite_wal_backup_consistency(db, tmp_path, monkeypatch):
    import backup
    import config

    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE wal_sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO wal_sample (value) VALUES ('from-wal')")
        conn.commit()
        monkeypatch.setattr(backup, "_BACKUP_DIR", str(tmp_path / "backups"))
        monkeypatch.setattr(backup.os, "getenv", lambda name, default="": "")

        backup_file = backup.backup_database()
    finally:
        conn.close()

    restored = tmp_path / "wal_restored.db"
    with gzip.open(backup_file, "rb") as f_in, open(restored, "wb") as f_out:
        f_out.write(f_in.read())
    restored_conn = sqlite3.connect(restored)
    try:
        assert restored_conn.execute("SELECT value FROM wal_sample").fetchone()[0] == "from-wal"
    finally:
        restored_conn.close()


def _pg_dump_with_required_tables() -> str:
    tables = [
        "users", "bookings", "booking_slots", "waitlist", "loyalty", "referrals",
        "reviews", "settings", "services", "scheduler_jobs", "slot_locks",
        "scheduler_locks", "unavailable_periods", "portfolio_photos", "social_links",
        "admin_audit_log",
    ]
    return "\n".join(f"CREATE TABLE public.{table} (id integer);" for table in tables) + "\nSELECT pg_catalog.setval('x', 1, false);"


def test_postgres_restore_check_requires_integrity_tables(tmp_path):
    import backup

    good = tmp_path / "good.sql.gz"
    good.write_bytes(gzip.compress(_pg_dump_with_required_tables().encode("utf-8")))
    assert backup.restore_check(str(good)) is True

    bad = tmp_path / "bad.sql.gz"
    incomplete = _pg_dump_with_required_tables().replace("CREATE TABLE public.unavailable_periods (id integer);\n", "")
    bad.write_bytes(gzip.compress(incomplete.encode("utf-8")))
    assert backup.restore_check(str(bad)) is False


def _pg_restore_drill_dump() -> str:
    simple_tables = [
        "users", "bookings", "waitlist", "loyalty", "referrals", "reviews",
        "settings", "services", "scheduler_jobs", "slot_locks", "scheduler_locks",
        "portfolio_photos", "social_links", "admin_audit_log",
    ]
    statements = [f"DROP TABLE IF EXISTS public.{table};" for table in [*simple_tables, "booking_slots", "unavailable_periods"]]
    statements.extend(f"CREATE TABLE public.{table} (id integer);" for table in simple_tables)
    statements.extend([
        "CREATE TABLE public.booking_slots (booking_id text NOT NULL, date text NOT NULL, master_key text NOT NULL, slot_time text NOT NULL, created_at text NOT NULL);",
        "CREATE UNIQUE INDEX idx_booking_slots_unique ON public.booking_slots(date, master_key, slot_time);",
        "CREATE TABLE public.unavailable_periods (id integer, date text, master text, master_key text, start_time text, end_time text, reason text, created_at text);",
        "INSERT INTO public.booking_slots VALUES ('b1', '2099-12-07', 'default', '10:00', '2099-01-01T00:00:00+00:00');",
        "INSERT INTO public.unavailable_periods VALUES (1, '2099-12-07', 'Anna', 'default', '12:00', '13:00', 'break', '2099-01-01T00:00:00+00:00');",
        "SELECT pg_catalog.setval('x', 1, false);",
    ])
    return "\n".join(statements)


def test_postgres_restore_check_real_mode_uses_psql(tmp_path):
    import backup

    dump = tmp_path / "restore.sql.gz"
    dump.write_bytes(gzip.compress(_pg_restore_drill_dump().encode("utf-8")))
    table_stdout = "\n".join(sorted(backup._REQUIRED_TABLES)).encode("utf-8")
    responses = [
        subprocess.CompletedProcess(["psql"], 0, stdout=b"", stderr=b""),
        subprocess.CompletedProcess(["psql"], 0, stdout=table_stdout, stderr=b""),
        subprocess.CompletedProcess(["psql"], 0, stdout=b"1|1\n", stderr=b""),
        subprocess.CompletedProcess(["psql"], 1, stdout=b"", stderr=b"ERROR: duplicate key value violates unique constraint"),
    ]

    with patch("backup.subprocess.run", side_effect=responses) as run:
        assert backup.restore_check(str(dump), postgres_restore_url="postgresql://restore-test/db") is True

    assert run.call_count == 4


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_DATABASE_URL"),
    reason="Set POSTGRES_TEST_DATABASE_URL to a disposable PostgreSQL DB and run: py -m pytest tests/test_production_hardening.py -k postgres_integration -v",
)
async def test_postgres_integration_concurrent_overlap_booking_slots(monkeypatch):
    import config
    import db as dbmod
    import storage

    monkeypatch.setenv("DATABASE_URL", os.environ["POSTGRES_TEST_DATABASE_URL"])
    await dbmod.init_pool()
    try:
        await storage.init_db()
        date = "2099-12-07"
        async with dbmod.acquire() as conn:
            await conn.execute("DELETE FROM booking_slots WHERE date=?", date)
            await conn.execute("DELETE FROM bookings WHERE date=?", date)
        results = await asyncio.gather(
            storage.save_booking(_booking(date=date, time="10:00", telegram_id=901, duration_minutes=60)),
            storage.save_booking(_booking(date=date, time="10:30", telegram_id=902, duration_minutes=60)),
        )
        assert sum(1 for result in results if result) == 1
    finally:
        await dbmod.close_pool()
        dbmod._use_pg = False


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_DATABASE_URL"),
    reason="Set POSTGRES_TEST_DATABASE_URL to a disposable PostgreSQL DB and run: py -m pytest tests/test_production_hardening.py -k postgres_integration_slot_lock -v",
)
async def test_postgres_integration_slot_lock_owner_cannot_be_stolen(monkeypatch):
    import db as dbmod
    import storage

    monkeypatch.setenv("DATABASE_URL", os.environ["POSTGRES_TEST_DATABASE_URL"])
    await dbmod.init_pool()
    try:
        await storage.init_db()
        date = "2099-12-08"
        async with dbmod.acquire() as conn:
            await conn.execute("DELETE FROM slot_locks WHERE date=?", date)

        results = await asyncio.gather(
            storage.create_slot_lock(date, "10:00", "Anna", duration_minutes=60, owner_id=1, owner_token="owner-a"),
            storage.create_slot_lock(date, "10:00", "Anna", duration_minutes=60, owner_id=2, owner_token="owner-b"),
        )

        assert sum(1 for result in results if result) == 1
        assert await storage.create_slot_lock(
            date, "10:00", "Anna", duration_minutes=60, owner_id=1, owner_token="owner-a"
        ) is (results[0] is True)
    finally:
        await dbmod.close_pool()
        dbmod._use_pg = False


@pytest.mark.skipif(
    not os.getenv("POSTGRES_RESTORE_TEST_DATABASE_URL"),
    reason="Set POSTGRES_RESTORE_TEST_DATABASE_URL to a disposable PostgreSQL DB and run: py -m pytest tests/test_production_hardening.py -k postgres_restore_drill -v",
)
def test_postgres_restore_drill_integration(tmp_path):
    import backup

    dump = tmp_path / "restore_drill.sql.gz"
    dump.write_bytes(gzip.compress(_pg_restore_drill_dump().encode("utf-8")))

    assert backup.restore_check(str(dump), postgres_restore_url=os.environ["POSTGRES_RESTORE_TEST_DATABASE_URL"]) is True
