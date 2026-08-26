# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Opt-in PostgreSQL integration coverage for demo_repository."""

import asyncio
import os

import pytest

import config
import db
import demo_repository


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def postgres_store(monkeypatch):
    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not set")
    monkeypatch.setattr(config, "DATABASE_URL", url)
    await db.init_pool()
    assert db.is_postgres(), "POSTGRES_TEST_DATABASE_URL must select PostgreSQL"
    monkeypatch.setattr(demo_repository, "_SCHEMA_READY", False)
    await demo_repository.init_demo_repository()
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM demo_requests")
        await conn.commit()
    try:
        yield
    finally:
        await db.close_pool()


async def test_postgres_schema_concurrency_claim_update_get(postgres_store):
    rows = await asyncio.gather(*[
        demo_repository.create_or_get_request("booking", 42, {"name": "A"}, "pg-same-key")
        for _ in range(4)
    ])
    assert len({row["id"] for row in rows}) == 1
    async with db.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM demo_requests WHERE idempotency_key = ?", "pg-same-key") == 1
    request_id = rows[0]["id"]
    assert await demo_repository.claim_notification(request_id)
    assert not await demo_repository.claim_notification(request_id)
    await demo_repository.update_notification(request_id, "failed", "test")
    stored = await demo_repository.get_request(request_id)
    assert stored["notification_status"] == "failed"
    assert stored["notification_error"] == "test"
