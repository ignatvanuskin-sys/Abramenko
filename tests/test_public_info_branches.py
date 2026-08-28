from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers.info as info
import keyboards
from studio_data import BRANCHES, MASTERS


def test_main_menu_exposes_only_information_and_branches():
    keyboard = keyboards.main_menu_kb()
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert callbacks == ["info", "branches"]
    assert labels == ["Информация", "Филиалы"]


def test_branches_keyboard_contains_every_branch_and_back_button():
    keyboard = keyboards.branches_kb(BRANCHES)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert callbacks[:-1] == [f"branch:{i}" for i in range(len(BRANCHES))]
    assert callbacks[-1] == "main_menu"


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
