import logging
import time
import asyncio
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

import config

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseMiddleware):
    """Hybrid rate limiter -- in-memory with fast access.
    MED-03 FIX: GC runs every 100 events AND on a periodic timer to prevent RAM growth."""
    def __init__(self, max_requests: int = 20, window: int = 60):
        self.max_requests = max_requests
        self.window = window
        self.user_requests: dict[int, list[float]] = {}
        self._event_counter = 0
        self._last_gc_time = time.time()
        super().__init__()

    def _cleanup_old_entries(self, now: float) -> None:
        """Remove entries for users with no recent requests."""
        cutoff = now - self.window
        stale_keys = [uid for uid, ts_list in self.user_requests.items()
                      if not ts_list or ts_list[-1] < cutoff]
        for k in stale_keys:
            del self.user_requests[k]

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return await handler(event, data)

        if user_id in config.ADMIN_IDS:
            # M-7 FIX: admins still rate-limited at 5x the normal limit
            # (prevents flood abuse if admin account is compromised)
            admin_key = f"admin_{user_id}"
            now2 = time.time()
            if admin_key not in self.user_requests:
                self.user_requests[admin_key] = []
            self.user_requests[admin_key] = [
                t for t in self.user_requests[admin_key] if (now2 - t) < self.window
            ]
            if len(self.user_requests[admin_key]) >= self.max_requests * 5:
                logger.warning(f"Admin rate limit hit for {user_id}")
                if isinstance(event, Message):
                    await event.answer("Слишком много запросов.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("Слишком много запросов.", show_alert=True)
                return
            self.user_requests[admin_key].append(now2)
            return await handler(event, data)

        now = time.time()

        # MED-03 FIX: periodic GC every 100 events OR every 60 seconds (timer-based)
        self._event_counter += 1
        time_since_gc = now - self._last_gc_time
        if self._event_counter % 100 == 0 or time_since_gc >= 60:
            self._cleanup_old_entries(now)
            self._last_gc_time = now

        if user_id not in self.user_requests:
            self.user_requests[user_id] = []

        self.user_requests[user_id] = [
            t for t in self.user_requests[user_id] if (now - t) < self.window
        ]

        if len(self.user_requests[user_id]) >= self.max_requests:
            logger.warning(f"Rate limit hit for user {user_id}: {len(self.user_requests[user_id])} requests in {self.window}s")
            if isinstance(event, Message):
                await event.answer("Слишком много запросов. Подождите немного.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Слишком много запросов. Пожалуйста подождите.", show_alert=True)
            return

        self.user_requests[user_id].append(now)
        return await handler(event, data)


class AdminCheckMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if user_id is not None and user_id in config.ADMIN_IDS:
            data["is_admin"] = True
        else:
            data["is_admin"] = False
        return await handler(event, data)
