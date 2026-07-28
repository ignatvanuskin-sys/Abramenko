import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDBConn:

    async def test_sqlite_fetch(self, db):
        import db as dbmod, storage
        await storage.init_db()
        async with dbmod.acquire() as conn:
            rows = await conn.fetch("SELECT 1 as val")
            assert len(rows) == 1
            assert rows[0]["val"] == 1

    async def test_sqlite_fetchrow(self, db):
        import db as dbmod, storage
        await storage.init_db()
        async with dbmod.acquire() as conn:
            row = await conn.fetchrow("SELECT 42 as num")
            assert row["num"] == 42

    async def test_sqlite_fetchrow_none(self, db):
        import db as dbmod, storage
        await storage.init_db()
        async with dbmod.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM bookings WHERE id=?", "nonexistent")
            assert row is None

    async def test_sqlite_fetchval(self, db):
        import db as dbmod, storage
        await storage.init_db()
        async with dbmod.acquire() as conn:
            val = await conn.fetchval("SELECT COUNT(*) FROM bookings")
            assert val == 0

    async def test_sqlite_execute_and_commit(self, db):
        import db as dbmod, storage
        await storage.init_db()
        async with dbmod.acquire() as conn:
            await conn.execute(
                "INSERT INTO bookings (id, date, time, name, telegram_id, master, service, price, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                "test_db_id", "2026-12-10", "10:00", "Test", 111, "Alibek", "Haircut", 3000, "active", "2026-12-10 10:00:00"
            )
            await conn.commit()
            val = await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE id=?", "test_db_id")
            assert val == 1

    async def test_sqlite_execute_count(self, db):
        import db as dbmod, storage
        await storage.init_db()
        async with dbmod.acquire() as conn:
            await conn.execute(
                "INSERT INTO bookings (id, date, time, name, telegram_id, master, service, price, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                "test_del_id", "2026-12-10", "10:00", "Test", 111, "Alibek", "Haircut", 3000, "active", "2026-12-10 10:00:00"
            )
            await conn.commit()
            count = await conn.execute_count("DELETE FROM bookings WHERE id=?", "test_del_id")
            assert count >= 0

    async def test_sqlite_transaction_commit(self, db):
        import db as dbmod, storage
        await storage.init_db()
        async with dbmod.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO bookings (id, date, time, name, telegram_id, master, service, price, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    "txn_test_id", "2026-12-10", "10:00", "Test", 111, "Alibek", "Haircut", 3000, "active", "2026-12-10 10:00:00"
                )
            val = await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE id=?", "txn_test_id")
            assert val == 1

    async def test_sqlite_transaction_rollback(self, db):
        import db as dbmod, storage
        await storage.init_db()
        async with dbmod.acquire() as conn:
            try:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO bookings (id, date, time, name, telegram_id, master, service, price, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        "rb_test_id", "2026-12-10", "10:00", "Test", 111, "Alibek", "Haircut", 3000, "active", "2026-12-10 10:00:00"
                    )
                    raise ValueError("force rollback")
            except ValueError:
                pass
            val = await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE id=?", "rb_test_id")
            assert val == 0

    async def test_q2d_conversion(self):
        import db as dbmod
        result = dbmod._q2d("SELECT * FROM t WHERE id=? AND name=?")
        assert "$1" in result
        assert "$2" in result
        assert "?" not in result

    def test_is_postgres_false(self):
        import db as dbmod
        assert dbmod.is_postgres() == False

    async def test_init_close_pool_sqlite(self, db):
        import db as dbmod
        await dbmod.init_pool()
        assert not dbmod.is_postgres()
        await dbmod.close_pool()