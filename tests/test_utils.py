
"""Tests for utils.py - send_with_retry, edit_with_retry"""
import pytest
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from unittest.mock import AsyncMock, MagicMock, patch, call
from aiogram.exceptions import TelegramBadRequest


class TestSendWithRetry:
    async def test_success_first_attempt(self):
        from utils import send_with_retry
        bot = AsyncMock()
        result = await send_with_retry(bot, 111, "hello")
        assert result is True
        bot.send_message.assert_called_once()

    async def test_retries_on_transient_error(self):
        from utils import send_with_retry
        bot = AsyncMock()
        bot.send_message.side_effect = [Exception("network error"), None]
        result = await send_with_retry(bot, 111, "hello", max_retries=2, retry_delay=0)
        assert result is True
        assert bot.send_message.call_count == 2

    async def test_returns_false_on_permanent_error(self):
        from utils import send_with_retry
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("bot was blocked by the user")
        result = await send_with_retry(bot, 111, "hello")
        assert result is False
        assert bot.send_message.call_count == 1  # no retry

    async def test_returns_false_on_forbidden(self):
        from utils import send_with_retry
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("forbidden")
        result = await send_with_retry(bot, 111, "hello")
        assert result is False

    async def test_all_retries_exhausted(self):
        from utils import send_with_retry
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("timeout")
        result = await send_with_retry(bot, 111, "hello", max_retries=2, retry_delay=0)
        assert result is False
        assert bot.send_message.call_count == 2

    async def test_passes_markup(self):
        from utils import send_with_retry
        from aiogram.types import InlineKeyboardMarkup
        bot = AsyncMock()
        kb = MagicMock(spec=InlineKeyboardMarkup)
        await send_with_retry(bot, 111, "hi", reply_markup=kb)
        call_kwargs = bot.send_message.call_args[1]
        assert call_kwargs.get("reply_markup") == kb


class TestEditWithRetry:
    async def test_success(self):
        from utils import edit_with_retry
        msg = AsyncMock()
        result = await edit_with_retry(msg, "new text")
        assert result is True
        msg.edit_text.assert_called_once()

    async def test_message_not_modified_silently_ignored(self):
        from utils import edit_with_retry
        msg = AsyncMock()
        msg.edit_text.side_effect = Exception("message is not modified")
        result = await edit_with_retry(msg, "same text")
        assert result is False  # returns False without retry
        assert msg.edit_text.call_count == 1  # no retry

    async def test_forbidden_not_retried(self):
        from utils import edit_with_retry
        msg = AsyncMock()
        msg.edit_text.side_effect = Exception("bot was blocked")
        result = await edit_with_retry(msg, "hi")
        assert result is False
        assert msg.edit_text.call_count == 1

    async def test_retries_on_transient(self):
        from utils import edit_with_retry
        msg = AsyncMock()
        msg.edit_text.side_effect = [Exception("network"), None]
        result = await edit_with_retry(msg, "hi", max_retries=2, retry_delay=0)
        assert result is True
        assert msg.edit_text.call_count == 2
