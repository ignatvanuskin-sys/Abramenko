import asyncio
import logging
import os
import sys

import config
import db as _db


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _check_database() -> None:
    await _db.init_pool()
    try:
        async with _db.acquire() as conn:
            value = await conn.fetchval("SELECT 1")
            if value != 1:
                raise RuntimeError("database ping returned unexpected value")

            # Verifies that storage schema is initialized without mutating data.
            await conn.fetchval("SELECT COUNT(*) FROM bookings")
    finally:
        await _db.close_pool()


async def run() -> int:
    try:
        if not config.BOT_TOKEN.strip():
            raise RuntimeError("BOT_TOKEN is not set")
        if config.BOT_MODE == "webhook" or config.is_production():
            config.validate_runtime_config()
        if config.REQUIRE_REDIS_FSM and not os.getenv("REDIS_URL", "").strip():
            raise RuntimeError("REDIS_URL is required by REQUIRE_REDIS_FSM")

        await _check_database()
        logger.info("healthcheck ok")
        return 0
    except Exception as e:
        logger.error("healthcheck failed: %s", e)
        return 1


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
