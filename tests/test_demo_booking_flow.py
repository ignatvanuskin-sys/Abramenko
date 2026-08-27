# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Async coverage for branch-first button booking."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers import demo_booking as flow
from helpers import make_callback, make_fsm, make_message


def live_state(data):
    state = make_fsm(data=data)
    async def update_data(**values): data.update(values)
    state.update_data.side_effect = update_data
    state.get_data.side_effect = lambda: dict(data)
    return state


def contact_message(phone="+77001234567", user_id=111):
    message = make_message(user_id=user_id)
    message.contact = MagicMock(phone_number=phone, user_id=user_id)
    return message


@pytest.mark.asyncio
async def test_branch_service_date_time_contact_confirm(monkeypatch):
    data = {}; state = live_state(data)
    await flow.start(make_callback("demo_book"), state)
    await flow.choose_branch(make_callback("db_branch:0"), state)
    with patch.object(flow.booking_engine, "_get_next_dates", AsyncMock(return_value=["2099-01-02"])):
        await flow.choose_service(make_callback("db_service:0"), state)
    with patch.object(flow.booking_engine, "_get_available_slots", AsyncMock(return_value={"12:00": "free"})):
        await flow.choose_date(make_callback("db_date:2099-01-02"), state)
        with patch.object(flow.storage, "create_slot_lock", AsyncMock(return_value=True)):
            await flow.choose_time(make_callback("db_time:12:00", user_id=42), state)
    await flow.name(make_message("Анна"), state); await flow.phone(contact_message(user_id=42), state)
    assert data["branch"] == flow.BRANCHES[0] and data["master"] == "Любой мастер"
    assert data["date"] == "2099-01-02" and data["time"] == "12:00"
    with patch.object(flow.storage, "save_booking", AsyncMock(return_value="book-1")), patch.object(flow, "finish", AsyncMock()) as finish:
        await flow.confirm(make_callback("db_confirm:0", user_id=42), state)
    finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_coloring_branch_and_photo_buttons():
    data = {"service": "AIRTOUCH"}; state = live_state(data)
    await flow.phone(contact_message(), state)
    await flow.hair_length(make_message("до плеч"), state)
    await flow.result(make_message("блонд"), state)
    await flow.last_coloring(make_message("год назад"), state)
    await flow.photo(make_callback("db_photo:0"), state)
    assert data["photo_ready"] == "да" and "год назад" in data["additional"]


@pytest.mark.asyncio
async def test_slot_race_and_branch_resource_isolation():
    data = {"branch": flow.BRANCHES[0], "date": "2099-01-02", "duration_minutes": 30}; state = live_state(data)
    with patch.object(flow.booking_engine, "_get_available_slots", AsyncMock(return_value={"12:00": "free"})), patch.object(flow.storage, "create_slot_lock", AsyncMock(return_value=False)):
        callback = make_callback("db_time:12:00")
        await flow.choose_time(callback, state)
        assert callback.answer.await_args.kwargs["show_alert"] is True
    assert flow.resource({"branch": flow.BRANCHES[0]}) != flow.resource({"branch": flow.BRANCHES[1]})


@pytest.mark.asyncio
async def test_cancel_and_back_release_lock():
    data = {"branch": flow.BRANCHES[0], "date": "2099-01-02", "time": "12:00", "lock_token": "x"}; state = live_state(data)
    with patch.object(flow.storage, "release_slot_lock", AsyncMock()) as release:
        await flow.cancel(make_callback("demo_booking_cancel"), state)
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_schedule_renders_no_slots_button():
    data = {"branch": flow.BRANCHES[0], "duration_minutes": 30, "eligible_dates": ["2099-01-02"]}; state = live_state(data)
    callback = make_callback("db_date:2099-01-02")
    with patch.object(flow.booking_engine, "_get_available_slots", AsyncMock(return_value={})):
        await flow.choose_date(callback, state)
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == ["no_slots", "db_back:date", "demo_booking_cancel"]
    assert "go_to_waitlist" not in callbacks


@pytest.mark.asyncio
async def test_booking_save_failure_has_real_recovery_button_and_does_not_duplicate():
    data = {"branch": flow.BRANCHES[0], "date": "2099-01-02", "time": "12:00", "name": "Анна", "service": "Женская стрижка", "lock_token": "token"}; state = live_state(data)
    first = make_callback("db_confirm:0", user_id=42)
    with patch.object(flow.storage, "save_booking", AsyncMock(return_value="book-1")) as save, patch.object(flow, "finish", AsyncMock(side_effect=RuntimeError("repository offline"))):
        await flow.confirm(first, state)
    markup = first.message.edit_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "db_recover_request"
    retry = make_callback("db_recover_request", user_id=42)
    with patch.object(flow.storage, "save_booking", AsyncMock()) as repeated_save, patch.object(flow, "finish", AsyncMock()) as finish:
        await flow.recover_request_callback(retry, state)
    repeated_save.assert_not_awaited(); finish.assert_awaited_once()
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_confirm_conflict_does_not_finish():
    data = {"branch": flow.BRANCHES[0], "date": "2099-01-02", "time": "12:00", "name": "Анна", "service": "Женская стрижка"}; state = live_state(data)
    callback = make_callback("db_confirm:0")
    with patch.object(flow.storage, "save_booking", AsyncMock(return_value=None)), patch.object(flow, "finish", AsyncMock()) as finish:
        await flow.confirm(callback, state)
    finish.assert_not_awaited(); assert callback.answer.await_args.kwargs["show_alert"] is True
