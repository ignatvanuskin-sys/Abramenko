import asyncio
import logging
import os
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import ErrorEvent, Message

import config
import db as _db_module
import keyboards
from config import BOT_TOKEN, load_config_from_db
from emoji_config import E
from handlers.admin import router as admin_router
from handlers.booking import router as booking_router
from handlers.info import router as info_router
from handlers.start import router as start_router
from middleware import AdminCheckMiddleware, RateLimitMiddleware
from monitoring import get_health_status, start_monitoring
from scheduler import shutdown_scheduler, start_scheduler
from storage import delete_old_scheduler_jobs, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _mask_url(url: str) -> str:
    if "@" not in url:
        return url
    prefix, suffix = url.split("@", 1)
    parts = prefix.rsplit(":", 1)
    if len(parts) != 2:
        return url
    return f"{parts[0]}:****@{suffix}"


def _create_bot() -> Bot:
    proxy = os.getenv("PROXY_URL", None)
    if proxy:
        from aiohttp import ClientTimeout
        from aiogram.client.session.aiohttp import AiohttpSession

        timeout = ClientTimeout(total=60, connect=30, sock_connect=30, sock_read=30)
        session = AiohttpSession(proxy=proxy, timeout=timeout)
        logger.info("Using proxy: %s", proxy)
        return Bot(token=BOT_TOKEN, session=session)

    logger.info("No proxy configured, using direct connection")
    return Bot(token=BOT_TOKEN)


async def _create_fsm_storage():
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            from aiogram.fsm.storage.redis import RedisStorage

            storage = RedisStorage.from_url(redis_url)
            logger.info("Using Redis FSM storage: %s", _mask_url(redis_url))
            return storage
        except Exception as e:
            if config.REQUIRE_REDIS_FSM:
                raise RuntimeError(
                    f"REDIS_URL is set but Redis FSM storage is unavailable: {e}"
                ) from e
            logger.warning("Redis not available, falling back to FileStorage: %s", e)

    # REQUIRE_REDIS_FSM is the definitive flag — overrides APP_ENV
    if config.REQUIRE_REDIS_FSM:
        raise RuntimeError(
            "REQUIRE_REDIS_FSM=true but no REDIS_URL found. "
            "Set REDIS_URL env var or REQUIRE_REDIS_FSM=false."
        )

    from fsm_storage import FileStorage

    logger.warning(
        "Using FileStorage FSM (single-instance only). "
        "Set REDIS_URL with REQUIRE_REDIS_FSM=true for cluster-safe FSM."
    )
    return FileStorage()


def _register_dispatcher(dp: Dispatcher, bot: Bot) -> None:
    dp.message.middleware(RateLimitMiddleware(max_requests=20, window=60))
    dp.callback_query.middleware(RateLimitMiddleware(max_requests=20, window=60))

    dp.message.middleware(AdminCheckMiddleware())
    dp.callback_query.middleware(AdminCheckMiddleware())

    dp.include_router(start_router)
    dp.include_router(booking_router)
    dp.include_router(info_router)
    dp.include_router(admin_router)

    from aiogram import Router as _FBRouter
    from aiogram.types import CallbackQuery as CQ

    _fallback = _FBRouter(name="fallback")

    @_fallback.message(F.text, ~F.text.regexp(r"^/"), StateFilter(None))
    async def fsm_fallback_handler(message: Message, state: FSMContext):
        await message.answer(
            f"{E.INFO} Напишите /start для начала работы.",
            reply_markup=keyboards.back_to_main_kb(),
            parse_mode="HTML",
        )

    @_fallback.callback_query()
    async def callback_fallback_handler(callback: CQ, state: FSMContext):
        await callback.answer("Сессия устарела. Нажмите /start", show_alert=True)

    dp.include_router(_fallback)

    @dp.error()
    async def global_error_handler(event: ErrorEvent):
        import traceback as _tb

        exc = event.exception
        err_text = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))[:3000]
        logger.error("Global error: %s", exc, exc_info=True)

        user_errors = (KeyError, ValueError, AttributeError)
        if not isinstance(exc, user_errors):
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        "⚠️ <b>Ошибка бота</b>\n" + f"<pre>{err_text}</pre>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        try:
            if event.update.message:
                await event.update.message.answer("Произошла ошибка. Попробуйте позже.")
            elif event.update.callback_query:
                await event.update.callback_query.answer("Произошла ошибка. Попробуйте позже.", show_alert=True)
        except Exception:
            pass


async def _set_bot_commands(bot: Bot) -> None:
    from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

    user_commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="me", description="Мой профиль и записи"),
        BotCommand(command="about", description="О мастере"),
        BotCommand(command="contacts", description="Контакты"),
        BotCommand(command="master", description="О мастере"),
        BotCommand(command="waitlist", description="Мой лист ожидания"),
        BotCommand(command="cancel", description="Отменить запись"),
        BotCommand(command="help", description="Справка по командам"),
    ]
    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())

    admin_commands = user_commands + [BotCommand(command="admin", description="Панель администратора")]
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logger.warning("Could not set admin commands for %s: %s", admin_id, e)

    logger.info("Bot commands set")


async def _startup(bot: Bot) -> None:
    try:
        await _db_module.init_pool()
        await init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}", exc_info=True)
        # Graceful degradation: bot can still show /start, but booking won't work
        return

    from storage import cleanup_slot_locks_on_startup, get_all_settings

    try:
        await cleanup_slot_locks_on_startup()
    except Exception as e:
        logger.warning(f"Slot lock cleanup failed (non-fatal): {e}")

    try:
        existing = await get_all_settings()
        if not existing:
            logger.info("First startup detected: saving default config to DB")
            await config.save_config_to_db()
    except Exception as e:
        logger.warning("First-startup config save failed: %s", e)

    try:
        await load_config_from_db()
        logger.info("Config loaded from DB")
    except Exception as e:
        logger.warning(f"Failed to load config from DB (using defaults): {e}")

    try:
        await _set_bot_commands(bot)
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")

    if not config.ADMIN_IDS:
        logger.warning("WARNING: ADMIN_IDS is empty! No one can access the admin panel.")
        logger.warning("Set ADMIN_IDS in .env: ADMIN_IDS=your_telegram_id")

    try:
        await delete_old_scheduler_jobs()
    except Exception as e:
        logger.warning(f"Failed to cleanup old scheduler jobs: {e}")

    try:
        from scheduler import auto_complete_booking as _auto_complete
        from storage import get_past_bookings_for_completion

        past_bookings = await get_past_bookings_for_completion()
        if past_bookings:
            logger.info("Found %s past-due bookings to auto-complete", len(past_bookings))
            for booking in past_bookings:
                try:
                    await _auto_complete(bot, booking)
                except Exception as e:
                    logger.error("Failed to auto-complete %s: %s", booking["id"], e)
    except Exception as e:
        logger.error("Startup past-due recovery failed: %s", e)

    try:
        await start_scheduler(bot)
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

    start_monitoring()

    try:
        health = await get_health_status()
        logger.info("Health: %s", health)
    except Exception as e:
        logger.warning(f"Health check failed: {e}")


async def _shutdown(bot: Bot, dp: Dispatcher) -> None:
    shutdown_scheduler()
    with suppress(Exception):
        await dp.storage.close()
        await dp.storage.wait_closed()
    with suppress(Exception):
        await bot.session.close()
    with suppress(Exception):
        await _db_module.close_pool()
    logger.info("Bot stopped")


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook deleted, pending updates dropped")
    except Exception as e:
        logger.warning("delete_webhook failed (non-critical): %s", e)

    from aiogram.exceptions import TelegramConflictError

    retries = config.POLLING_CONFLICT_RETRIES
    delay = max(0.1, config.POLLING_CONFLICT_RETRY_DELAY)
    for attempt in range(retries):
        try:
            logger.info("Starting polling (attempt %s/%s)...", attempt + 1, retries)
            await dp.start_polling(bot, drop_pending_updates=True)
            break
        except TelegramConflictError:
            if attempt < retries - 1:
                logger.warning(
                    "TelegramConflictError: another instance active. Retry %s/%s in %ss...",
                    attempt + 1,
                    retries,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("TelegramConflictError: max retries exceeded.")
                raise


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _stop() -> None:
        if not stop_event.is_set():
            logger.info("Shutdown signal received")
            stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError, ValueError):
            with suppress(Exception):
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(_stop))


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    config.validate_webhook_config()

    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    webhook_path = config.WEBHOOK_PATH if config.WEBHOOK_PATH.startswith("/") else f"/{config.WEBHOOK_PATH}"
    webhook_url = f"{config.validate_webhook_url()}{webhook_path}"
    secret_token = config.WEBHOOK_SECRET_TOKEN or None

    await bot.set_webhook(webhook_url, secret_token=secret_token, drop_pending_updates=False)
    logger.info("Webhook set: %s", webhook_url)

    app = web.Application()

    async def health_handler(request):
        health = await get_health_status()
        status = 200 if health.get("status") == "ok" else 503
        return web.json_response(health, status=status)

    async def metrics_handler(request):
        """MED-04 FIX: Prometheus-compatible /metrics endpoint."""
        from monitoring import get_metrics_snapshot
        metrics = get_metrics_snapshot()
        lines = []
        # Format Prometheus-style metrics
        for key, value in metrics.items():
            if isinstance(value, (int, float)):
                safe_key = key.replace("(", "").replace(")", "").replace(" ", "_")
                lines.append(f"# HELP bot_{safe_key} {key}")
                lines.append(f"# TYPE bot_{safe_key} gauge")
                lines.append(f"bot_{safe_key} {value}")
        lines.append(f"# HELP bot_uptime_seconds Bot uptime in seconds")
        lines.append(f"# TYPE bot_uptime_seconds gauge")
        lines.append(f"bot_uptime_seconds {metrics.get('uptime_seconds', 0)}")
        return web.Response(
            text="\n".join(lines),
            content_type="text/plain; charset=utf-8",
        )

    app.router.add_get("/health", health_handler)
    app.router.add_get("/ready", health_handler)
    app.router.add_get("/metrics", metrics_handler)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret_token).register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEBHOOK_HOST, config.WEBHOOK_PORT)
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)

    try:
        await site.start()
        logger.info("Webhook server listening on %s:%s", config.WEBHOOK_HOST, config.WEBHOOK_PORT)
        await stop_event.wait()
    finally:
        await runner.cleanup()


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Please configure .env")
        return
    try:
        config.validate_runtime_config()
    except config.ConfigError as e:
        logger.error("Configuration error: %s", e)
        return

    bot = _create_bot()
    fsm_storage = await _create_fsm_storage()
    dp = Dispatcher(storage=fsm_storage)
    _register_dispatcher(dp, bot)

    shutdown_timeout = 30  # seconds
    try:
        await _startup(bot)
        if config.BOT_MODE == "webhook":
            await _run_webhook(bot, dp)
        elif config.BOT_MODE == "polling":
            await _run_polling(bot, dp)
        else:
            raise RuntimeError("BOT_MODE must be 'polling' or 'webhook'")
    except asyncio.TimeoutError:
        logger.error("Bot operation timed out, shutting down...")
    except Exception as e:
        logger.critical(f"Fatal error in main loop: {e}", exc_info=True)
        # Notify admins even during fatal error
        try:
            from monitoring import increment_counter
            increment_counter("errors_total")
            for admin_id in config.ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, f"⚠️ <b>Критическая ошибка</b>\n<code>{str(e)[:500]}</code>", parse_mode="HTML")
                except Exception:
                    pass
        except Exception:
            pass
    finally:
        try:
            await asyncio.wait_for(_shutdown(bot, dp), timeout=shutdown_timeout)
            logger.info("Graceful shutdown completed")
        except asyncio.TimeoutError:
            logger.warning(f"Shutdown timed out after {shutdown_timeout}s, forcing exit...")


if __name__ == "__main__":
    asyncio.run(main())
