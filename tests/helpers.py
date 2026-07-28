
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from unittest.mock import AsyncMock, MagicMock


SAMPLE_BOOKING = {
    "date": "2026-12-07",
    "time": "10:00",
    "name": "Ivan",
    "telegram_id": 111,
    "username": "testuser",
    "master": "Alibek",
    "service": "Haircut",
    "price": 3000,
}


def make_user(user_id=111, username="testuser", first_name="Test"):
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.first_name = first_name
    return user


def make_message(text="", user_id=111, chat_id=111):
    msg = MagicMock()
    msg.from_user = make_user(user_id)
    msg.chat = MagicMock(id=chat_id)
    msg.text = text
    msg.bot = AsyncMock()
    msg.answer = AsyncMock()
    msg.edit_text = AsyncMock()
    msg.reply = AsyncMock()
    msg.contact = None
    return msg


def make_callback(data="", user_id=111, message=None):
    cb = MagicMock()
    cb.from_user = make_user(user_id)
    cb.data = data
    cb.message = message or make_message(user_id=user_id)
    cb.bot = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def make_fsm(state=None, data=None):
    fsm = AsyncMock()
    fsm.get_state = AsyncMock(return_value=state)
    fsm.get_data = AsyncMock(return_value=data or {})
    fsm.set_state = AsyncMock()
    fsm.update_data = AsyncMock()
    fsm.clear = AsyncMock()
    return fsm
