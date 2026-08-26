#!/usr/bin/env python3
# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
"""Apply database schema upgrades for the current application version."""

import asyncio
import logging
import sys

import db
import storage


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> int:
    try:
        await db.init_pool()
        await storage.init_db()
        logger.info("Database migration completed")
        return 0
    except Exception as e:
        logger.error("Database migration failed: %s", e, exc_info=True)
        return 1
    finally:
        try:
            await db.close_pool()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
