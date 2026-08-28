"""Real SQLite coverage for Abramenko booking lock and persistence guarantees."""

import asyncio
import json
from datetime import timedelta

import pytest

import config
import db as db_module
import storage
from tz_utils import get_now

DATE = "2099-01-05"
TIME = "12:00"
BRANCH_1 = "Abramenko Studio — ул. Букетова, 61|Любой мастер"
BRANCH_2 = "Мадам — ул. Жамбыла, 127|Любой мастер"


def booking(branch, user_id):
    return {
        "date": DATE,
        "time": TIME,
        "name": "Анна",
        "telegram_id": user_id,
        "username": "",
        "master": branch,
        "master_key": branch,
        "service": "Женская стрижка",
        "price": 1000,
        "duration_minutes": 30,
    }


@pytest.mark.asyncio
async def test_concurrent_same_branch_lock_has_exactly_one_winner(db):
    results = await asyncio.gather(
        storage.create_slot_lock(DATE, TIME, BRANCH_1, owner_id=1, owner_token="one"),
        storage.create_slot_lock(DATE, TIME, BRANCH_1, owner_id=2, owner_token="two"),
    )
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_same_slot_different_branches_both_lock(db):
    results = await asyncio.gather(
        storage.create_slot_lock(DATE, TIME, BRANCH_1, owner_id=1, owner_token="one"),
        storage.create_slot_lock(DATE, TIME, BRANCH_2, owner_id=2, owner_token="two"),
    )
    assert results == [True, True]


@pytest.mark.asyncio
async def test_save_booking_requires_live_owned_lock_and_consumes_it(db):
    assert await storage.create_slot_lock(DATE, TIME, BRANCH_1, owner_id=1, owner_token="right")
    assert await storage.save_booking(booking(BRANCH_1, 1), owner_id=2, owner_token="wrong", require_live_lock=True) is None
    booking_id = await storage.save_booking(booking(BRANCH_1, 1), owner_id=1, owner_token="right", require_live_lock=True)
    assert booking_id
    async with db_module.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM slot_locks") == 0


@pytest.mark.asyncio
async def test_save_booking_rejects_expired_lock(db):
    assert await storage.create_slot_lock(DATE, TIME, BRANCH_1, owner_id=1, owner_token="expired")
    async with db_module.acquire() as conn:
        await conn.execute(
            "UPDATE slot_locks SET expires_at=?",
            (get_now(config.TIMEZONE) - timedelta(minutes=1)).isoformat(),
        )
        await conn.commit()
    assert await storage.save_booking(booking(BRANCH_1, 1), owner_id=1, owner_token="expired", require_live_lock=True) is None


@pytest.mark.asyncio
async def test_concurrent_bookings_in_different_branches_both_complete(db):
    lock_results = await asyncio.gather(
        storage.create_slot_lock(DATE, TIME, BRANCH_1, owner_id=101, owner_token="branch-one-load"),
        storage.create_slot_lock(DATE, TIME, BRANCH_2, owner_id=202, owner_token="branch-two-load"),
    )
    assert lock_results == [True, True]
    booking_ids = [
        await storage.save_booking(
            booking(BRANCH_1, 101), owner_id=101, owner_token="branch-one-load", require_live_lock=True
        ),
        await storage.save_booking(
            booking(BRANCH_2, 202), owner_id=202, owner_token="branch-two-load", require_live_lock=True
        ),
    ]
    assert all(booking_ids)
    assert len(set(booking_ids)) == 2
    async with db_module.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_id, master_key FROM bookings WHERE date=? AND time=? ORDER BY telegram_id",
            DATE, TIME,
        )
    assert [(row["telegram_id"], row["master_key"]) for row in rows] == [
        (101, BRANCH_1),
        (202, BRANCH_2),
    ]


@pytest.mark.asyncio
async def test_external_sync_payload_preserves_branch(monkeypatch):
    import external_sync

    captured = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, url, data, headers):
            captured.update(url=url, body=data, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(config, "EXTERNAL_SYNC_URL", "https://crm.example.test/webhook")
    monkeypatch.setattr(config, "EXTERNAL_SYNC_SECRET", "test-secret")
    monkeypatch.setattr(external_sync.aiohttp, "ClientSession", FakeSession)
    assert await external_sync.sync_booking_created(
        booking(BRANCH_2, 202) | {"branch": "Мадам — ул. Жамбыла, 127"}, "booking-2"
    )
    payload = json.loads(captured["body"])
    assert payload["branch"] == "Мадам — ул. Жамбыла, 127"
    assert captured["headers"]["X-Signature-SHA256"]

    monkeypatch.setattr(config, "EXTERNAL_SYNC_URL", "")
    monkeypatch.setattr(config, "EXTERNAL_SYNC_SECRET", "")


def test_branch_booking_payload_contains_branch_field():
    from pathlib import Path

    source = Path("handlers/booking.py").read_text(encoding="utf-8")
    assert '"branch": data.get("branch", "")' in source
    assert "branch=BRANCHES[branch_index]" in source


def test_storage_facade_has_no_undefined_master_price_reference():
    from pathlib import Path

    source = Path("storage.py").read_text(encoding="utf-8")
    assert "async def get_master_service_price" in source
    assert "services_repository" in source


def test_production_observability_files_are_present():
    from pathlib import Path

    assert Path("observability.py").exists()
    assert Path("monitoring/prometheus.yml").exists()
    assert Path("monitoring/grafana/provisioning/datasources/prometheus.yml").exists()


def test_external_sync_has_retry_and_hmac_contract():
    from pathlib import Path

    source = Path("external_sync.py").read_text(encoding="utf-8")
    assert "X-Signature-SHA256" in source
    assert "EXTERNAL_SYNC_RETRIES" in source
    assert "external_sync_failed" in source
