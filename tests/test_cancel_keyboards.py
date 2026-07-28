import pytest
from unittest.mock import AsyncMock, MagicMock, patch

MOCK_BOOKINGS = [
    {"id": "abc123", "date": "2026-06-14", "time": "11:00",
     "master": "Алибек", "service": "Стрижка", "price": 3000, "status": "active"},
    {"id": "def456", "date": "2026-06-15", "time": "14:00",
     "master": "Дамир", "service": "Борода", "price": 1500, "status": "active"},
]

def make_msg(text="/cancel", user_id=123):
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.bot = MagicMock()
    return msg

# cancel_bookings_kb
def test_cancel_kb_has_buttons():
    import keyboards
    kb = keyboards.cancel_bookings_kb(MOCK_BOOKINGS)
    btns = [b for row in kb.inline_keyboard for b in row]
    assert len(btns) == 3
    datas = [b.callback_data for b in btns]
    assert "cancel_book:abc123" in datas
    assert "cancel_book:def456" in datas
    assert "main_menu" in datas

def test_cancel_kb_empty():
    import keyboards
    kb = keyboards.cancel_bookings_kb([])
    btns = [b for row in kb.inline_keyboard for b in row]
    assert len(btns) == 1
    assert btns[0].callback_data == "main_menu"

def test_cancel_kb_long_name():
    import keyboards
    bk = [{"id": "x1", "date": "2026-06-14", "time": "10:00",
            "master": "ОченьДлинноеИмяMasterName", "service": "s",
            "price": 100, "status": "active"}]
    kb = keyboards.cancel_bookings_kb(bk)
    btn = kb.inline_keyboard[0][0]
    assert len(btn.text) < 100

# /cancel command
@pytest.mark.asyncio
async def test_cancel_clears_fsm():
    from handlers.start import cmd_cancel_universal
    msg = make_msg("/cancel")
    state = AsyncMock()
    state.get_state = AsyncMock(return_value="SomeState:step")
    # L-6 FIX: explicitly return empty dict so data.get() doesn't produce unawaited coroutines
    state.get_data = AsyncMock(return_value={})
    state.clear = AsyncMock()
    with patch("handlers.start.send_with_retry", new_callable=AsyncMock) as m:
        await cmd_cancel_universal(msg, state)
    state.clear.assert_called_once()
    assert "Действие отменено" in m.call_args[0][2]

@pytest.mark.asyncio
async def test_cancel_no_bookings():
    from handlers.start import cmd_cancel_universal
    msg = make_msg("/cancel")
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    with (patch("storage.get_user_bookings", new_callable=AsyncMock, return_value=[]),
         patch("handlers.start.send_with_retry", new_callable=AsyncMock) as m):
        await cmd_cancel_universal(msg, state)
    assert "Нет активных записей" in m.call_args[0][2]

@pytest.mark.asyncio
async def test_cancel_shows_buttons():
    from handlers.start import cmd_cancel_universal
    msg = make_msg("/cancel")
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    with (patch("storage.get_user_bookings", new_callable=AsyncMock, return_value=MOCK_BOOKINGS),
         patch("handlers.start.send_with_retry", new_callable=AsyncMock) as m):
        await cmd_cancel_universal(msg, state)
    assert "reply_markup" in m.call_args[1]

@pytest.mark.asyncio
async def test_cancel_with_id_success():
    """UX-FIX: /cancel BOOKING_ID now shows confirmation screen first."""
    from handlers.start import cmd_cancel_universal
    msg = make_msg("/cancel abc123")
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    with (patch("storage.get_user_bookings", new_callable=AsyncMock, return_value=MOCK_BOOKINGS),
         patch("handlers.start.send_with_retry", new_callable=AsyncMock) as m):
        await cmd_cancel_universal(msg, state)
    kwargs = m.call_args[1]
    # Must show confirmation screen (confirm_cancel_kb), NOT immediately cancel
    from keyboards import confirm_cancel_kb
    assert kwargs["reply_markup"] is not None
    # Text should include warning / confirmation language
    text = m.call_args[0][2]
    assert "Подтвердите" in text  # Подтвердите отмену
    assert "Алибек" in text  # master name shown

@pytest.mark.asyncio
async def test_cancel_with_id_not_found():
    """UX-FIX: /cancel BOOKING_ID with unknown ID shows not-found message."""
    from handlers.start import cmd_cancel_universal
    msg = make_msg("/cancel badid")
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    with (patch("storage.get_user_bookings", new_callable=AsyncMock, return_value=[]),
         patch("handlers.start.send_with_retry", new_callable=AsyncMock) as m):
        await cmd_cancel_universal(msg, state)
    assert "не найдена" in m.call_args[0][2]

@pytest.mark.asyncio
async def test_cancel_no_text():
    from handlers.start import cmd_cancel_universal
    msg = make_msg()
    msg.text = None
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    with patch("handlers.start.send_with_retry", new_callable=AsyncMock) as m:
        await cmd_cancel_universal(msg, state)
    m.assert_called_once()

# Source code checks
def test_admin_complete_uses_premium_emoji():
    from handlers import admin
    import inspect
    src = inspect.getsource(admin.cb_admin_complete_booking)
    assert "✅ Запись {booking_id} завершена" not in src
    assert "E.CHECK" in src
    assert "E.USER" in src

def test_admin_cancel_uses_premium_emoji():
    from handlers import admin
    import inspect
    src = inspect.getsource(admin.cb_admin_cancel_booking)
    assert "E.CHECK" in src
    assert "E.ID" in src

def test_help_hides_admin_command():
    from handlers import start
    import inspect
    src = inspect.getsource(start.cmd_help)
    assert "• /admin" not in src
