# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import demo_repository
from handlers import demo
from studio_data import BRANCHES, FAQ, SERVICES
from helpers import make_callback, make_fsm, make_message


def _future_date(days: int = 10) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")


def test_catalog_and_faq_are_confirmed_only():
    assert SERVICES == ["Женская стрижка", "Мужская стрижка", "Детская стрижка", "Окрашивание", "Тонирование", "Уход"]
    assert BRANCHES == ["Филиал 1", "Филиал 2"]
    assert all("администратор" in FAQ[key].lower() for key in ("prices", "addresses", "hours"))


def test_validation_and_html_contract():
    assert demo.normalize_phone("+7 (700) 123-45-67") == "+77001234567"
    assert demo.normalize_phone("87001234567") is None
    assert demo.valid_date("31.02") is False            # invalid day-month
    assert demo.valid_date("31.02.2026") is False       # invalid day-month, full format
    assert demo.valid_date("15.09") is False            # short date without year is rejected
    assert demo.valid_date(_future_date(-10)) is False   # past full date is rejected
    assert demo.valid_date(_future_date()) is True       # future full date is accepted
    text = demo.admin_card("Запись", {"name": "<b>x</b>"})
    assert "&lt;b&gt;x&lt;/b&gt;" in text
    assert "Дата: —" in text and "Источник: Telegram-демо" in text


@pytest.mark.asyncio
async def test_repository_persists_full_payload_and_is_idempotent(db):
    payload = {"service": "Окрашивание", "branch": "Филиал 1", "date": "15.09", "time": "12:00", "name": "Анна", "phone": "+77001234567", "master": None, "hair_length": "до плеч"}
    first, second = await asyncio.gather(
        demo_repository.create_or_get_request("booking", 42, payload, "same-key"),
        demo_repository.create_or_get_request("booking", 42, payload, "same-key"),
    )
    assert first["id"] == second["id"]
    assert first["payload"] == payload
    assert first["status"] == "pending_confirmation"
    assert first["notification_status"] == "pending"


@pytest.mark.asyncio
async def test_booking_order_phone_to_master_then_coloring_questions():
    state = make_fsm(data={"service": "Окрашивание"})
    message = make_message("+7 700 123 45 67")
    await demo.phone(message, state)
    state.set_state.assert_awaited_once_with(demo.Booking.master)
    state = make_fsm(data={"service": "Окрашивание"})
    message = make_message("нет")
    await demo.master(message, state)
    state.set_state.assert_awaited_once_with(demo.Booking.hair_length)


@pytest.mark.asyncio
async def test_finish_empty_admin_persists_and_does_not_claim_sent(monkeypatch, db):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", None)
    message = make_message(user_id=7)
    state = make_fsm(data={"name": "Анна", "phone": "+77001234567", "branch": "Филиал 1"})
    await demo.finish(message, state, "Стать моделью", "model")
    request_id = (await demo_repository.create_or_get_request(
        "model", 7, demo.normalized_payload(await state.get_data()),
        demo.hashlib.sha256(f"7:model:{demo.json.dumps(demo.normalized_payload(await state.get_data()), ensure_ascii=False, sort_keys=True)}".encode()).hexdigest(),
    ))["id"]
    stored = await demo_repository.get_request(request_id)
    assert stored["notification_status"] == "failed"
    assert stored["notification_error"] == "ADMIN_CHAT_ID не настроен"
    assert "сохранена" in message.answer.await_args.args[0]
    assert "передана" not in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_finish_send_failure_records_error(monkeypatch, db):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    message = make_message(user_id=7); message.bot.send_message.side_effect = RuntimeError("offline")
    data = {"name": "Анна", "phone": "+77001234567", "branch": "Филиал 1"}
    state = make_fsm(data=data)
    await demo.finish(message, state, "Вакансия", "vacancy")
    payload = demo.normalized_payload(data)
    key = demo.hashlib.sha256(f"7:vacancy:{demo.json.dumps(payload, ensure_ascii=False, sort_keys=True)}".encode()).hexdigest()
    row = await demo_repository.create_or_get_request("vacancy", 7, payload, key)
    stored = await demo_repository.get_request(row["id"])
    assert stored["notification_status"] == "failed"
    assert stored["notification_error"] == "offline"
    assert "не отправлено" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_finish_success_and_repeat_confirmation(monkeypatch):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    message = make_message(user_id=7); state = make_fsm(data={"name": "Анна"})
    pending = {"id": "r3", "notification_status": "pending"}
    with patch.object(demo.demo_repository, "create_or_get_request", AsyncMock(return_value=pending)), patch.object(demo.demo_repository, "claim_notification", AsyncMock(return_value=True)), patch.object(demo.demo_repository, "update_notification", AsyncMock()) as update:
        await demo.finish(message, state, "Курс", "course")
    update.assert_awaited_once_with("r3", "sent")
    assert "передана" in message.answer.await_args.args[0]
    repeated = make_message(user_id=7); repeated_state = make_fsm(data={"name": "Анна"})
    sent = {"id": "r3", "notification_status": "sent"}
    with patch.object(demo.demo_repository, "create_or_get_request", AsyncMock(return_value=sent)), patch.object(demo.demo_repository, "update_notification", AsyncMock()) as update:
        await demo.finish(repeated, repeated_state, "Курс", "course")
    repeated.bot.send_message.assert_not_awaited(); update.assert_not_awaited()


@pytest.mark.asyncio
async def test_finish_send_success_db_update_failure_says_honest_message(monkeypatch):
    """Post-send update_notification failure falls back to 'failed' and tells user."""
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    message = make_message(user_id=7); state = make_fsm(data={"name": "Анна"})
    pending = {"id": "r4", "notification_status": "pending"}
    with (
        patch.object(demo.demo_repository, "create_or_get_request", AsyncMock(return_value=pending)),
        patch.object(demo.demo_repository, "claim_notification", AsyncMock(return_value=True)),
        patch.object(demo.demo_repository, "update_notification", AsyncMock(side_effect=[RuntimeError("db lost"), None])) as update,
    ):
        await demo.finish(message, state, "Запись", "booking")
    # sent write raised, then the fallback 'failed' write succeeded
    update.assert_any_call("r4", "sent")
    update.assert_any_call("r4", "failed", "sent but status not recorded")
    assert "передана" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_booking_master_max_length_rejected():
    """Free-text field 'master' rejects input over MAX_TEXT_FIELD."""
    state = make_fsm(data={"service": "Женская стрижка"})
    message = make_message("A" * 201)
    await demo.master(message, state)
    assert "максимум" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_booking_hair_length_max_length_rejected():
    state = make_fsm()
    message = make_message("B" * 201)
    await demo.hair_length(message, state)
    assert "максимум" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_booking_result_max_length_rejected():
    state = make_fsm()
    message = make_message("C" * 201)
    await demo.result(message, state)
    assert "максимум" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_booking_last_coloring_max_length_rejected():
    state = make_fsm()
    message = make_message("D" * 201)
    await demo.last_coloring(message, state)
    assert "максимум" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_lead_rejects_invalid_phone_and_branch():
    fields = [("phone", "Телефон?"), ("branch", "Филиал?")]
    state = make_fsm(data={"lead_fields": fields, "lead_index": 0})
    message = make_message("wrong")
    await demo.lead_field(message, state)
    state.update_data.assert_not_awaited()
    state = make_fsm(data={"lead_fields": fields, "lead_index": 1})
    message = make_message("Филиал 9")
    await demo.lead_field(message, state)
    state.update_data.assert_not_awaited()
