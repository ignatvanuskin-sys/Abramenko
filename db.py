import os, re, logging
from contextlib import asynccontextmanager
from typing import Any, Optional

logger = logging.getLogger(__name__)
_pool = None
_use_pg: bool = False

async def init_pool() -> None:
    global _pool, _use_pg
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        import asyncpg
        _pool = await asyncpg.create_pool(
            database_url, min_size=2, max_size=10,
            command_timeout=30,
        )
        _use_pg = True
        logger.info("db.py: PostgreSQL pool initialised")
    else:
        _use_pg = False
        logger.info("db.py: SQLite mode (no DATABASE_URL)")

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

def is_postgres() -> bool:
    return _use_pg

def _q2d(sql: str) -> str:
    """Convert ? placeholders to $1, $2, ... for PostgreSQL."""
    n = 0
    r = []
    for c in sql:
        if c == "?":
            n += 1
            r.append(f"${n}")
        else:
            r.append(c)
    return "".join(r)

_RX_INSERT_OR_IGNORE = re.compile(
    r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE
)

class DBConn:
    __slots__ = ("_conn", "_is_pg", "_tx_depth")

    def __init__(self, conn, is_pg: bool):
        self._conn = conn
        self._is_pg = is_pg
        self._tx_depth = 0

    async def fetch(self, sql: str, *a) -> list[dict]:
        if self._is_pg:
            return [dict(r) for r in await self._conn.fetch(_q2d(sql), *a)]
        import aiosqlite
        self._conn.row_factory = aiosqlite.Row
        async with self._conn.execute(sql, a) as c:
            return [dict(r) for r in await c.fetchall()]

    async def fetchrow(self, sql: str, *a) -> Optional[dict]:
        if self._is_pg:
            r = await self._conn.fetchrow(_q2d(sql), *a)
            return dict(r) if r else None
        import aiosqlite
        self._conn.row_factory = aiosqlite.Row
        async with self._conn.execute(sql, a) as c:
            r = await c.fetchone()
            return dict(r) if r else None

    async def fetchval(self, sql: str, *a) -> Any:
        if self._is_pg:
            return await self._conn.fetchval(_q2d(sql), *a)
        async with self._conn.execute(sql, a) as c:
            r = await c.fetchone()
            return r[0] if r else None

    async def execute(self, sql: str, *a) -> None:
        """Execute a statement.  INSERT OR IGNORE is auto-translated for PG."""
        if self._is_pg:
            # Detect BEFORE substitution
            _had_ignore = bool(_RX_INSERT_OR_IGNORE.search(sql))
            sql = _RX_INSERT_OR_IGNORE.sub("INSERT INTO", sql)
            if _had_ignore and "ON CONFLICT" not in sql.upper():
                sql = sql.rstrip() + " ON CONFLICT DO NOTHING"
            await self._conn.execute(_q2d(sql), *a)
        else:
            await self._conn.execute(sql, a)

    async def commit(self) -> None:
        if not self._is_pg:
            await self._conn.commit()

    async def execute_count(self, sql: str, *a) -> int:
        """Execute DELETE/UPDATE and return number of affected rows (both backends)."""
        if self._is_pg:
            status = await self._conn.execute(_q2d(sql), *a)
            try:
                return int(str(status).split()[-1])
            except Exception:
                return 0
        else:
            async with self._conn.execute(sql, a) as cur:
                count = cur.rowcount
            if self._tx_depth == 0:
                await self._conn.commit()
            return count if count >= 0 else 0

    async def upsert(self, table: str, conflict_cols: list[str], data: dict) -> None:
        """INSERT ... ON CONFLICT (conflict_cols) DO UPDATE SET ...  (both backends)."""
        cols = list(data.keys())
        vals = list(data.values())
        cols_sql = ", ".join(cols)
        if self._is_pg:
            pg_ph = ", ".join(f"${i + 1}" for i in range(len(vals)))
            conflict = ", ".join(conflict_cols)
            update_set = ", ".join(
                f"{c}=EXCLUDED.{c}" for c in cols if c not in conflict_cols
            )
            sql = (
                f"INSERT INTO {table} ({cols_sql}) VALUES ({pg_ph}) "
                f"ON CONFLICT ({conflict}) DO "
                + (f"UPDATE SET {update_set}" if update_set else "NOTHING")
            )
            await self._conn.execute(sql, *vals)
        else:
            ph = ", ".join("?" for _ in vals)
            await self._conn.execute(
                f"INSERT OR REPLACE INTO {table} ({cols_sql}) VALUES ({ph})", vals
            )
            await self._conn.commit()

    @asynccontextmanager
    async def transaction(self):
        if self._is_pg:
            async with self._conn.transaction():
                yield self
        else:
            self._tx_depth += 1
            await self._conn.execute("BEGIN EXCLUSIVE")
            try:
                yield self
                await self._conn.commit()
            except Exception:
                await self._conn.execute("ROLLBACK")
                raise
            finally:
                self._tx_depth -= 1


@asynccontextmanager
async def acquire():
    """Acquire a DB connection (PostgreSQL pool or SQLite file)."""
    if _use_pg:
        async with _pool.acquire() as raw:
            yield DBConn(raw, True)
    else:
        import aiosqlite, config
        async with aiosqlite.connect(config.DB_PATH) as raw:
            yield DBConn(raw, False)
