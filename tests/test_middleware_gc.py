
"""Tests for updated middleware.py GC fix (MED-03)."""
import time, pytest, sys, pathlib
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


def _mw(max_requests=20, window=60):
    from middleware import RateLimitMiddleware
    return RateLimitMiddleware(max_requests=max_requests, window=window)


def _ev(user_id=999):
    from helpers import make_message
    return make_message(user_id=user_id)


class TestGcCounter:

    async def test_counter_increments(self):
        mw = _mw()
        assert mw._event_counter == 0
        await mw(AsyncMock(return_value="ok"), _ev(777), {})
        assert mw._event_counter == 1

    async def test_gc_runs_at_100(self):
        mw = _mw()
        calls = []
        orig = mw._cleanup_old_entries
        mw._cleanup_old_entries = lambda now: (calls.append(now), orig(now))
        for i in range(100):
            await mw(AsyncMock(return_value="ok"), _ev(i + 200), {})
        assert len(calls) == 1

    async def test_gc_runs_at_multiples_of_100(self):
        mw = _mw()
        calls = []
        orig = mw._cleanup_old_entries
        mw._cleanup_old_entries = lambda now: (calls.append(now), orig(now))
        for i in range(300):
            await mw(AsyncMock(return_value="ok"), _ev(i + 500), {})
        assert len(calls) == 3

    async def test_gc_not_before_100(self):
        mw = _mw()
        calls = []
        mw._cleanup_old_entries = lambda now: calls.append(now)
        for i in range(99):
            await mw(AsyncMock(return_value="ok"), _ev(i + 700), {})
        assert len(calls) == 0


class TestGcRemovesStale:

    def test_removes_expired_users(self):
        mw = _mw(window=60)
        old = time.time() - 120
        mw.user_requests = {1001: [old], 1002: [time.time()]}
        mw._cleanup_old_entries(time.time())
        assert 1001 not in mw.user_requests
        assert 1002 in mw.user_requests

    def test_removes_empty_list(self):
        mw = _mw()
        mw.user_requests = {1003: [], 1004: [time.time()]}
        mw._cleanup_old_entries(time.time())
        assert 1003 not in mw.user_requests

    def test_keeps_fresh_user(self):
        mw = _mw(window=60)
        mw.user_requests = {1005: [time.time() - 10]}
        mw._cleanup_old_entries(time.time())
        assert 1005 in mw.user_requests

    async def test_gc_clears_stale_at_100th_event(self):
        mw = _mw(window=1)
        old = time.time() - 5
        for uid in range(1, 100):
            mw.user_requests[uid] = [old]
        mw._event_counter = 99
        await mw(AsyncMock(return_value="ok"), _ev(9999), {})
        for uid in range(1, 100):
            assert uid not in mw.user_requests


class TestRateLimitWorks:

    async def test_passes_within_limit(self):
        mw = _mw(max_requests=3)
        handler = AsyncMock(return_value="ok")
        ev = _ev(8001)
        for _ in range(3):
            await mw(handler, ev, {})
        assert handler.call_count == 3

    async def test_blocks_over_limit(self):
        from aiogram.types import Message
        mw = _mw(max_requests=2)
        handler = AsyncMock(return_value="ok")
        ev = MagicMock(spec=Message)
        ev.from_user = MagicMock(id=8002)
        ev.answer = AsyncMock()
        for _ in range(3):
            await mw(handler, ev, {})
        assert handler.call_count == 2

    async def test_no_from_user_passes(self):
        mw = _mw()
        handler = AsyncMock(return_value="ok")
        ev = MagicMock()
        ev.from_user = None
        await mw(handler, ev, {})
        assert handler.called
