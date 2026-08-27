# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Complete async dialog coverage for the Abramenko runtime demo."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import demo_repository
from handlers import demo
from helpers import make_callback, make_fsm, make_message


BOOKING_DATE = (datetime.now() + timedelta(days=10)).strftime("%d.%m.%Y")


@pytest.mark.asyncio
async def test_regular_booking_happy_path_confirm_persist_and_admin_card(monkeypatch, db):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    data = {}
    state = make_fsm(data=data)

    async def update_data(**values):
        data.update(values)
    state.update_data.side_effect = update_data
    state.get_data.side_effect = lambda: dict(data)

    await demo.service(make_callback("demo_service:0"), state)
    await demo.branch(make_callback("demo_branch:0"), state)
    await demo.date(make_message(BOOKING_DATE), state)
    await demo.time(make_message("12:00"), state)
    await demo.name(make_message("Анна"), state)
    await demo.phone(make_message("+7 700 123 45 67"), state)
    await demo.master(make_message("нет"), state)
    assert data["service"] == "Женская стрижка"
    assert data["additional"] == "Услуга: Женская стрижка"

    callback = make_callback("demo_confirm:0", user_id=42)
    await demo.confirm(callback, state)
    callback.message.bot.send_message.assert_awaited_once()
    admin_text = callback.message.bot.send_message.await_args.args[1]
    assert "Тип: Запись" in admin_text and "Анна" in admin_text and BOOKING_DATE in admin_text
    rows = await _requests()
    assert len(rows) == 1 and rows[0]["request_type"] == "booking"
    assert rows[0]["notification_status"] == "sent"


@pytest.mark.asyncio
async def test_coloring_collects_all_extra_questions():
    data = {"service": "AIRTOUCH"}
    state = make_fsm(data=data)

    async def update_data(**values):
        data.update(values)
    state.update_data.side_effect = update_data
    state.get_data.side_effect = lambda: dict(data)

    await demo.master(make_message("Ирина"), state)
    await demo.hair_length(make_message("до плеч"), state)
    await demo.result(make_message("холодный блонд"), state)
    await demo.last_coloring(make_message("три месяца назад"), state)
    await demo.photo(make_message("да"), state)
    assert data["hair_length"] == "до плеч"
    assert data["desired_result"] == "холодный блонд"
    assert data["last_coloring"] == "три месяца назад"
    assert data["photo_ready"] == "да"
    assert all(value in data["additional"] for value in ("до плеч", "холодный блонд", "три месяца назад", "Фото: да"))
    state.set_state.assert_awaited_with(demo.Booking.confirm)


@pytest.mark.asyncio
async def test_faq_callback_edits_message_and_answers():
    callback = make_callback("demo_faq:prices")
    await demo.faq(callback)
    callback.message.edit_text.assert_awaited_once()
    assert "администратор" in callback.message.edit_text.await_args.args[0].lower()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_model_full_request_confirm_persist_and_card(monkeypatch, db):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 99)
    fields = demo.LEADS["model"][1]
    data = {"lead_key": "model", "lead_title": "Стать моделью", "lead_fields": fields, "lead_index": 0}
    state = make_fsm(data=data)

    async def update_data(**values):
        data.update(values)
    state.update_data.side_effect = update_data
    state.get_data.side_effect = lambda: dict(data)

    for value in ("Анна", "+7 700 123 45 67", "AIRTOUCH", "https://example.test/p", "Мадам — ул. Жамбыла, 127"):
        await demo.lead_field(make_message(value), state)
    assert data["lead_index"] == len(fields)
    state.set_state.assert_awaited_with(demo.Lead.confirm)

    callback = make_callback("demo_lead_confirm:0", user_id=77)
    await demo.lead_confirm(callback, state)
    callback.message.bot.send_message.assert_awaited_once()
    card = callback.message.bot.send_message.await_args.args[1]
    assert "Тип: Стать моделью" in card and "Мадам — ул. Жамбыла, 127" in card
    rows = await _requests()
    assert len(rows) == 1 and rows[0]["request_type"] == "model"
    assert rows[0]["payload"]["portfolio"] == "https://example.test/p"
    assert rows[0]["notification_status"] == "sent"


@pytest.mark.asyncio
async def test_demo_uses_exactly_one_admin_recipient(monkeypatch, db):
    monkeypatch.setattr(demo.config, "DEMO_ADMIN_CHAT_ID", 10)
    monkeypatch.setattr(demo.config, "ADMIN_IDS", [10, 20])
    message = make_message(user_id=7)
    state = make_fsm(data={"name": "Анна"})
    await demo.finish(message, state, "Курс", "course")
    message.bot.send_message.assert_awaited_once()
    assert message.bot.send_message.await_args.args[0] == 10


async def _requests():
    import db
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM demo_requests ORDER BY created_at")
    return [demo_repository._as_dict(row) for row in rows]
