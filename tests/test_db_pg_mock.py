import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDBConnPGMethods:
    """Cover PostgreSQL code paths by mocking _is_pg=True"""

    async def test_fetch_pg_path(self):
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[MagicMock(items=lambda: [("val", 1)])])
        # Use a real dict row mock
        row = MagicMock()
        row.__iter__ = MagicMock(return_value=iter([("val", 1)]))
        mock_conn.fetch = AsyncMock(return_value=[row])
        conn = dbmod.DBConn(mock_conn, True)
        # fetch should call conn.fetch and convert rows
        mock_conn.fetch = AsyncMock(return_value=[])
        result = await conn.fetch("SELECT 1")
        assert result == []
        mock_conn.fetch.assert_called_once()

    async def test_fetchrow_pg_returns_none(self):
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)
        conn = dbmod.DBConn(mock_conn, True)
        result = await conn.fetchrow("SELECT * FROM t WHERE id=$1", "x")
        assert result is None

    async def test_fetchrow_pg_returns_row(self):
        import db as dbmod
        mock_conn = AsyncMock()
        mock_row = MagicMock()
        mock_row.items = MagicMock(return_value=[("id", "x"), ("name", "Test")])
        mock_conn.fetchrow = AsyncMock(return_value=mock_row)
        conn = dbmod.DBConn(mock_conn, True)
        result = await conn.fetchrow("SELECT * FROM t WHERE id=$1", "x")
        assert result is not None

    async def test_fetchval_pg(self):
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=42)
        conn = dbmod.DBConn(mock_conn, True)
        result = await conn.fetchval("SELECT COUNT(*) FROM t")
        assert result == 42

    async def test_execute_pg_insert_or_ignore(self):
        """Lines 85-89: INSERT OR IGNORE -> INSERT INTO ... ON CONFLICT DO NOTHING"""
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        conn = dbmod.DBConn(mock_conn, True)
        await conn.execute("INSERT OR IGNORE INTO t (id) VALUES (?)", "x")
        args = mock_conn.execute.call_args[0]
        assert "ON CONFLICT DO NOTHING" in args[0]
        assert "INSERT INTO" in args[0]

    async def test_execute_pg_regular_insert(self):
        """Regular INSERT without OR IGNORE"""
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        conn = dbmod.DBConn(mock_conn, True)
        await conn.execute("INSERT INTO t (id) VALUES (?)", "x")
        args = mock_conn.execute.call_args[0]
        assert "$1" in args[0]

    async def test_execute_count_pg(self):
        """Lines 100-104: execute_count for PostgreSQL"""
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="DELETE 3")
        conn = dbmod.DBConn(mock_conn, True)
        count = await conn.execute_count("DELETE FROM t WHERE id=$1", "x")
        assert count == 3

    async def test_execute_count_pg_parse_error(self):
        """execute_count returns 0 on parse failure"""
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UNKNOWN")
        conn = dbmod.DBConn(mock_conn, True)
        count = await conn.execute_count("DELETE FROM t WHERE id=$1", "x")
        assert count == 0

    async def test_upsert_pg(self):
        """Lines 117-127: upsert for PostgreSQL"""
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        conn = dbmod.DBConn(mock_conn, True)
        await conn.upsert(
            "users",
            ["telegram_id"],
            {"telegram_id": 1, "first_name": "Test", "username": "t"}
        )
        mock_conn.execute.assert_called_once()
        sql = mock_conn.execute.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO UPDATE SET" in sql

    async def test_upsert_pg_only_conflict_cols(self):
        """upsert with all cols as conflict cols -> DO NOTHING"""
        import db as dbmod
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        conn = dbmod.DBConn(mock_conn, True)
        await conn.upsert("t", ["id"], {"id": "x"})
        sql = mock_conn.execute.call_args[0][0]
        assert "NOTHING" in sql

    async def test_transaction_pg(self):
        """Lines 138-139: transaction for PostgreSQL"""
        import db as dbmod
        mock_conn = AsyncMock()
        mock_txn = AsyncMock()
        mock_conn.transaction = MagicMock(return_value=mock_txn)
        mock_txn.__aenter__ = AsyncMock(return_value=None)
        mock_txn.__aexit__ = AsyncMock(return_value=False)
        conn = dbmod.DBConn(mock_conn, True)
        async with conn.transaction() as c:
            assert c is conn

    async def test_acquire_pg_path(self):
        """Lines 154-155: acquire() uses PG pool when _use_pg=True"""
        import db as dbmod
        mock_pool = AsyncMock()
        mock_raw = AsyncMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_raw)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        original_use_pg = dbmod._use_pg
        original_pool = dbmod._pool
        try:
            dbmod._use_pg = True
            dbmod._pool = mock_pool
            async with dbmod.acquire() as conn:
                assert isinstance(conn, dbmod.DBConn)
                assert conn._is_pg is True
        finally:
            dbmod._use_pg = original_use_pg
            dbmod._pool = original_pool

    async def test_init_pool_pg(self):
        """Lines 13-19: init_pool with DATABASE_URL uses asyncpg"""
        import db as dbmod
        mock_pool = AsyncMock()
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://user:pass@localhost/db"}), \
             patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool):
            await dbmod.init_pool()
            assert dbmod._use_pg is True
            assert dbmod._pool is mock_pool
        # Restore
        dbmod._use_pg = False
        dbmod._pool = None

    async def test_close_pool_none(self):
        """Lines 27-28: close_pool does nothing if _pool is None"""
        import db as dbmod
        original = dbmod._pool
        dbmod._pool = None
        await dbmod.close_pool()
        assert dbmod._pool is None
        dbmod._pool = original