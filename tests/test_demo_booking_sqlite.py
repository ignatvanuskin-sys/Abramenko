# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Real SQLite coverage for Abramenko booking lock and persistence guarantees."""

import asyncio
from datetime import timedelta

import pytest

import config
import db as db_module
import storage
from tz_utils import get_now

DATE = "2099-01-05"
TIME = "12:00"
BRANCH_1 = "AIRTOUCH — ул. Букетова, 61|Любой мастер"
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
