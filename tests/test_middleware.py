
"""Tests for middleware.py"""
import pytest
import sys, pathlib, asyncio, time
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from unittest.mock import AsyncMock, MagicMock, patch


class TestRateLimitMiddleware:
    def _make_middleware(self, max_requests=5, window=60):
        from middleware import RateLimitMiddleware
        return RateLimitMiddleware(max_requests=max_requests, window=window)

    def _make_message_event(self, user_id=999):
        from helpers import make_message
        return make_message(user_id=user_id)

    async def test_first_request_passes(self):
        mw = self._make_middleware()
        handler = AsyncMock(return_value="ok")
        event = self._make_message_event()
        result = await mw(handler, event, {})
        assert result == "ok"
        handler.assert_called_once()

    async def test_within_limit_passes(self):
        mw = self._make_middleware(max_requests=3)
        handler = AsyncMock(return_value="ok")
        event = self._make_message_event(user_id=500)
        for _ in range(3):
            result = await mw(handler, event, {})
        assert handler.call_count == 3

    async def test_over_limit_blocks(self):
        from aiogram.types import Message
        mw = self._make_middleware(max_requests=2)
        handler = AsyncMock(return_value="ok")
        event = MagicMock(spec=Message)
        event.from_user = MagicMock(id=501)
        event.answer = AsyncMock()
        await mw(handler, event, {})
        await mw(handler, event, {})
        # 3rd request should be blocked
        await mw(handler, event, {})
        assert handler.call_count == 2
        event.answer.assert_called()

    async def test_admin_bypasses_rate_limit(self):
        import config
        config.ADMIN_IDS = [777]
        mw = self._make_middleware(max_requests=1)
        handler = AsyncMock(return_value="ok")
        event = self._make_message_event(user_id=777)
        # Make 5 requests - all should pass for admin
        for _ in range(5):
            await mw(handler, event, {})
        assert handler.call_count == 5

    async def test_no_user_passes(self):
        mw = self._make_middleware()
        handler = AsyncMock(return_value="ok")
        event = MagicMock()
        event.from_user = None
        result = await mw(handler, event, {})
        assert result == "ok"

    async def test_callback_rate_limited_shows_alert(self):
        from middleware import RateLimitMiddleware
        from aiogram.types import CallbackQuery
        mw = RateLimitMiddleware(max_requests=1)
        handler = AsyncMock(return_value="ok")
        event = MagicMock(spec=CallbackQuery)
        event.from_user = MagicMock(id=502)
        event.answer = AsyncMock()
        # First passes, second blocked
        await mw(handler, event, {})
        await mw(handler, event, {})
        event.answer.assert_called_with("Слишком много запросов. Пожалуйста подождите.", show_alert=True)


class TestAdminCheckMiddleware:
    async def test_admin_flagged(self):
        import config
        config.ADMIN_IDS = [100]
        from middleware import AdminCheckMiddleware
        mw = AdminCheckMiddleware()
        handler = AsyncMock(return_value="ok")
        from helpers import make_message
        event = make_message(user_id=100)
        data = {}
        await mw(handler, event, data)
        assert data["is_admin"] is True

    async def test_non_admin_flagged_false(self):
        import config
        config.ADMIN_IDS = [100]
        from middleware import AdminCheckMiddleware
        mw = AdminCheckMiddleware()
        handler = AsyncMock(return_value="ok")
        from helpers import make_message
        event = make_message(user_id=999)
        data = {}
        await mw(handler, event, data)
        assert data["is_admin"] is False

    async def test_no_user_flagged_false(self):
        from middleware import AdminCheckMiddleware
        mw = AdminCheckMiddleware()
        handler = AsyncMock(return_value="ok")
        event = MagicMock()
        event.from_user = None
        data = {}
        await mw(handler, event, data)
        assert data["is_admin"] is False
