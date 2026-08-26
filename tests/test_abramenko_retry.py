# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Async retry and delivery-claim coverage for the Abramenko demo."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import demo_repository
from handlers import demo
from helpers import make_callback, make_fsm, make_message


def _row(status="failed", user=42):
    return {"id": "a" * 36, "telegram_id": user, "request_type": "booking", "payload": {"name": "Анна"}, "notification_status": status}


def test_retry_callback_is_within_telegram_limit():
    callback = demo.retry_kb("a" * 36).inline_keyboard[0][0].callback_data
    assert len(callback.encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_retry_success_from_persisted_failed_row(monkeypatch):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    callback = make_callback("demo_retry:" + "a" * 36, user_id=42)
    callback.message.bot.send_message = AsyncMock()
    with patch.object(demo.demo_repository, "get_request", AsyncMock(return_value=_row())), patch.object(demo.demo_repository, "claim_notification", AsyncMock(return_value=True)) as claim, patch.object(demo.demo_repository, "update_notification", AsyncMock()) as update:
        await demo.retry(callback)
    claim.assert_awaited_once(); update.assert_awaited_once_with("a" * 36, "sent")


@pytest.mark.asyncio
async def test_telegram_failure_persists_error_and_returns_retry_button(monkeypatch):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    message = make_message(user_id=42); message.bot.send_message.side_effect = RuntimeError("offline")
    with patch.object(demo.demo_repository, "create_or_get_request", AsyncMock(return_value={"id": "b" * 36, "notification_status": "pending"})), patch.object(demo.demo_repository, "claim_notification", AsyncMock(return_value=True)), patch.object(demo.demo_repository, "update_notification", AsyncMock()) as update:
        await demo.finish(message, make_fsm(data={"name": "Анна"}), "Запись", "booking")
    update.assert_awaited_once_with("b" * 36, "failed", "offline")
    assert message.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.encode().__len__() <= 64


@pytest.mark.asyncio
async def test_retry_ownership_denial_and_already_claimed_sent_no_duplicate():
    callback = make_callback("demo_retry:" + "c" * 36, user_id=42)
    for row, claim in ((_row(user=7), True), (_row(status="sent"), True), (_row(), False)):
        with patch.object(demo.demo_repository, "get_request", AsyncMock(return_value=row)), patch.object(demo.demo_repository, "claim_notification", AsyncMock(return_value=claim)) as claim_mock:
            await demo.retry(callback)
        callback.message.bot.send_message.assert_not_awaited()
        claim_mock.assert_not_awaited() if row["telegram_id"] != 42 or row["notification_status"] == "sent" else None


@pytest.mark.asyncio
async def test_concurrent_retry_only_one_claim_sends(monkeypatch):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    callback1 = make_callback("demo_retry:" + "d" * 36, user_id=42)
    callback2 = make_callback("demo_retry:" + "d" * 36, user_id=42)
    gate = asyncio.Lock()
    async def claim(_):
        if gate.locked(): return False
        await gate.acquire(); return True
    with patch.object(demo.demo_repository, "get_request", AsyncMock(return_value=_row())), patch.object(demo.demo_repository, "claim_notification", side_effect=claim), patch.object(demo.demo_repository, "update_notification", AsyncMock()):
        await asyncio.gather(demo.retry(callback1), demo.retry(callback2))
    assert callback1.message.bot.send_message.await_count + callback2.message.bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_retry_admin_id_not_configured_shows_specific_error(monkeypatch):
    """retry shows actionable error when ADMIN_CHAT_ID is not set."""
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", None)
    callback = make_callback("demo_retry:" + "e" * 36, user_id=42)
    with patch.object(demo.demo_repository, "get_request", AsyncMock(return_value=_row())):
        await demo.retry(callback)
    assert "ADMIN_CHAT_ID" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_retry_post_send_db_update_failure_says_honest_message(monkeypatch):
    """Post-send update_notification failure in retry falls back to 'failed'."""
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    callback = make_callback("demo_retry:" + "f" * 36, user_id=42)
    with (
        patch.object(demo.demo_repository, "get_request", AsyncMock(return_value=_row())),
        patch.object(demo.demo_repository, "claim_notification", AsyncMock(return_value=True)),
        patch.object(demo.demo_repository, "update_notification", AsyncMock(side_effect=[RuntimeError("db lost"), None])) as update,
    ):
        await demo.retry(callback)
    update.assert_any_call("f" * 36, "sent")
    update.assert_any_call("f" * 36, "failed", "sent but status not recorded")
    assert "Отправлено" in callback.answer.await_args.args[0]
