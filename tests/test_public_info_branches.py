from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers.info as info
import handlers.booking as booking
import keyboards
from studio_data import BRANCHES, MASTERS


def test_main_menu_exposes_requested_public_actions_only():
    keyboard = keyboards.main_menu_kb()
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert callbacks == ["book", "prices", "my_bookings", "info", "about_master", "portfolio"]
    assert labels == ["Записаться", "Услуги и цены", "Мои записи", "Информация", "Мастера", "Портфолио"]


def test_branches_keyboard_contains_every_branch_and_back_button():
    keyboard = keyboards.branches_kb(BRANCHES)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert callbacks[:-1] == [f"branch:{i}" for i in range(len(BRANCHES))]
    assert callbacks[-1] == "main_menu"


@pytest.mark.asyncio
async def test_booking_branch_selection_filters_specialists(monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr(booking, "_safe_edit", edit)
    callback = SimpleNamespace(message=object(), answer=AsyncMock(), data="book_branch:1")
    state = AsyncMock()

    await booking.cb_choose_branch(callback, state)

    state.update_data.assert_awaited_once_with(branch_index=1, branch=BRANCHES[1])
    state.set_state.assert_awaited_once_with(booking.BookingStates.choose_master)
    keyboard = edit.await_args.kwargs["reply_markup"]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "Елена" in labels and "Ольга" in labels
    assert "Алиса" not in labels and "Диана" not in labels
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_info_callback_renders_studio_information(monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr(info, "edit_with_retry", edit)
    callback = SimpleNamespace(message=object(), answer=AsyncMock(), data="info")

    await info.cb_info(callback)

    text = edit.await_args.args[1]
    assert "Abramenko Studio" in text
    assert "График работы" in text
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_branch_callback_renders_selected_branch(monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr(info, "edit_with_retry", edit)
    callback = SimpleNamespace(message=object(), answer=AsyncMock(), data="branch:0")

    await info.cb_branch(callback)

    text = edit.await_args.args[1]
    assert BRANCHES[0] in text
    assert any(name in text for name, _description, branch_index in MASTERS if branch_index == 0)
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_branch_callback_is_handled_without_edit(monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr(info, "edit_with_retry", edit)
    callback = SimpleNamespace(message=object(), answer=AsyncMock(), data="branch:999")

    await info.cb_branch(callback)

    edit.assert_not_awaited()
    callback.answer.assert_awaited_once_with("Филиал не найден", show_alert=True)


@pytest.mark.asyncio
async def test_branches_callback_renders_branch_selector(monkeypatch):
    edit = AsyncMock()
    monkeypatch.setattr(info, "edit_with_retry", edit)
    callback = SimpleNamespace(message=object(), answer=AsyncMock(), data="branches")

    await info.cb_branches(callback)

    assert "Филиалы" in edit.await_args.args[1]
    assert edit.await_args.kwargs["reply_markup"].inline_keyboard[-1][0].callback_data == "main_menu"
    callback.answer.assert_awaited_once()



def test_booking_step_labels_are_consistent_six_step_flow():
    import messages

    texts = [
        messages.CHOOSE_BRANCH,
        messages.CHOOSE_MASTER,
        messages.master_selected("Мастер", "5 лет", "Маникюр"),
        messages.service_selected("Маникюр", 1000),
        messages.date_selected("понедельник, 1 января"),
    ]
    for step, text in enumerate(texts, start=1):
        assert f"Шаг {step} из 6" in text


def test_main_menu_has_direct_master_and_portfolio_actions():
    keyboard = keyboards.main_menu_kb()
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks[-2:] == ["about_master", "portfolio"]


@pytest.mark.asyncio
async def test_no_slots_callback_uses_plain_alert_emoji():
    import handlers.booking as booking_handler

    callback = SimpleNamespace(answer=AsyncMock())
    await booking_handler.cb_no_slots(callback)
    alert_text = callback.answer.await_args.args[0]
    assert "<tg-emoji" not in alert_text
    assert "❌" not in alert_text
    assert "📦" in alert_text
