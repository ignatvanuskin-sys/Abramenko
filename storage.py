import uuid
import db as _db
from datetime import datetime, timedelta
import config
import os
import logging
from pathlib import Path
from tz_utils import get_now

logger = logging.getLogger(__name__)


class _BookingRejected(Exception):
    pass


class _SlotLockRejected(Exception):
    pass


def _increment_metric(name: str, amount: int = 1) -> None:
    try:
        from monitoring import increment_counter
        increment_counter(name, amount)
    except Exception:
        pass


def _log_event(event: str, **fields) -> None:
    try:
        from monitoring import log_event
        log_event(logger, event, **fields)
    except Exception:
        pass


def normalize_duration_minutes(duration_minutes: int | None) -> int:
    try:
        duration = int(duration_minutes or config.DEFAULT_SERVICE_DURATION_MINUTES)
    except (TypeError, ValueError):
        duration = config.DEFAULT_SERVICE_DURATION_MINUTES
    return duration if duration > 0 else config.DEFAULT_SERVICE_DURATION_MINUTES


def normalize_master_key(master_key: str | None = None) -> str:
    value = (master_key or getattr(config, "MASTER_KEY", "default") or "default").strip()
    return value or "default"


def time_to_minutes(time_str: str) -> int:
    hours, minutes = time_str.split(":", 1)
    return int(hours) * 60 + int(minutes)


def minutes_to_time(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def slot_times_for_range(start_time: str, duration_minutes: int | None) -> list[str]:
    duration = normalize_duration_minutes(duration_minutes)
    start = time_to_minutes(start_time)
    count = (duration + config.SLOT_STEP_MINUTES - 1) // config.SLOT_STEP_MINUTES
    return [minutes_to_time(start + i * config.SLOT_STEP_MINUTES) for i in range(count)]


def working_time_slots_for_date(date_str: str) -> list[str]:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return []
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    hours = config.WORKING_HOURS.get(day_names[d.weekday()])
    if not hours or len(hours) != 2:
        return []
    start_h, end_h = int(hours[0]), int(hours[1])
    if start_h >= end_h:
        return []
    slots = []
    for h in range(start_h, end_h):
        slots.append(f"{h:02d}:00")
        slots.append(f"{h:02d}:30")
    return slots


def booking_range_fits_working_day(date_str: str, start_time: str, duration_minutes: int | None) -> bool:
    available_slots = set(working_time_slots_for_date(date_str))
    required_slots = slot_times_for_range(start_time, duration_minutes)
    return bool(required_slots) and all(slot in available_slots for slot in required_slots)


def booking_time_is_far_enough(date_str: str, start_time: str) -> bool:
    try:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        h, m = [int(part) for part in start_time.split(":", 1)]
    except Exception:
        return False
    now = get_now(config.TIMEZONE)
    if selected_date < now.date():
        return False
    if selected_date > now.date():
        return True
    slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return slot_dt > now + timedelta(minutes=config.MIN_BOOKING_ADVANCE_MINUTES)


def time_ranges_overlap(start_a: str, duration_a: int | None, start_b: str, duration_b: int | None) -> bool:
    a1 = time_to_minutes(start_a)
    a2 = a1 + normalize_duration_minutes(duration_a)
    b1 = time_to_minutes(start_b)
    b2 = b1 + normalize_duration_minutes(duration_b)
    return a1 < b2 and b1 < a2


def _duration_between(start_time: str, end_time: str) -> int:
    return max(config.SLOT_STEP_MINUTES, time_to_minutes(end_time) - time_to_minutes(start_time))


def _period_overlaps(start_time: str, duration_minutes: int | None, period: dict) -> bool:
    return time_ranges_overlap(
        start_time,
        duration_minutes,
        period["start_time"],
        _duration_between(period["start_time"], period["end_time"]),
    )


def _is_unique_constraint_error(exc: Exception) -> bool:
    text = str(exc).lower()
    constraint = str(getattr(exc, "constraint_name", "") or "").lower()
    return (
        "unique" in text
        or "duplicate key" in text
        or "constraint failed" in text
        or bool(constraint)
    )


def _is_primary_key_conflict(exc: Exception) -> bool:
    text = str(exc).lower()
    constraint = str(getattr(exc, "constraint_name", "") or "").lower()
    return "bookings_pkey" in constraint or "primary key" in text or "bookings.id" in text


# ======================================================================
# Schema migration helpers
# ======================================================================

async def _get_table_columns(conn, table_name: str) -> set[str]:
    if _db.is_postgres():
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name=$1",
            table_name,
        )
        return {r["column_name"] for r in rows}
    else:
        rows = await conn.fetch(f"PRAGMA table_info({table_name})")
        await conn.commit()
        return {r["name"] for r in rows}


async def _add_column_if_missing(conn, table_name: str, column_name: str, definition: str) -> None:
    existing = await _get_table_columns(conn, table_name)
    if column_name not in existing:
        await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {definition}")
        await conn.commit()


async def _migrate_bookings_bonus_spent(conn) -> None:
    """Add bonus_spent column to bookings if absent."""
    await _add_column_if_missing(conn, "bookings", "bonus_spent", "bonus_spent INTEGER DEFAULT 0")


async def _migrate_bookings_comment(conn) -> None:
    """Add optional client comment column to bookings if absent."""
    await _add_column_if_missing(conn, "bookings", "comment", "comment TEXT DEFAULT ''")


async def _migrate_users_blocked(conn) -> None:
    """Add blacklist flag to users if absent."""
    await _add_column_if_missing(conn, "users", "blocked", "blocked INTEGER DEFAULT 0")


async def _migrate_duration_columns(conn) -> None:
    """Add duration metadata to existing databases if absent."""
    default_duration = config.DEFAULT_SERVICE_DURATION_MINUTES
    await _add_column_if_missing(
        conn,
        "bookings",
        "duration_minutes",
        f"duration_minutes INTEGER DEFAULT {default_duration}",
    )
    await _add_column_if_missing(
        conn,
        "waitlist",
        "duration_minutes",
        f"duration_minutes INTEGER DEFAULT {default_duration}",
    )
    await _add_column_if_missing(
        conn,
        "services",
        "duration_minutes",
        f"duration_minutes INTEGER DEFAULT {default_duration}",
    )


async def _migrate_master_key_columns(conn) -> None:
    default_key = normalize_master_key()
    default_key_sql = default_key.replace("'", "''")
    for table_name in ("bookings", "waitlist", "unavailable_periods", "slot_locks"):
        await _add_column_if_missing(
            conn,
            table_name,
            "master_key",
            f"master_key TEXT DEFAULT '{default_key_sql}'",
        )
        await conn.execute(
            f"UPDATE {table_name} SET master_key=? WHERE master_key IS NULL OR master_key=''",
            default_key,
        )
    await conn.commit()


async def _migrate_slot_lock_owner_columns(conn) -> None:
    await _add_column_if_missing(conn, "slot_locks", "owner_id", "owner_id BIGINT")
    await _add_column_if_missing(conn, "slot_locks", "owner_token", "owner_token TEXT DEFAULT ''")


async def _ensure_waitlist_unique_index(conn) -> None:
    await conn.execute(
        "UPDATE waitlist SET status='duplicate' WHERE id IN ("
        "SELECT id FROM ("
        "  SELECT id, ROW_NUMBER() OVER ("
        "    PARTITION BY telegram_id, date, master_key, time "
        "    ORDER BY id"
        "  ) AS rn "
        "  FROM waitlist WHERE status='waiting'"
        ") AS duplicates WHERE rn > 1)"
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_waitlist_unique_waiting "
        "ON waitlist(telegram_id, date, master_key, time) WHERE status='waiting'"
    )
    await conn.commit()


async def _ensure_unavailable_periods_table(conn) -> None:
    if _db.is_postgres():
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS unavailable_periods (
                id         SERIAL PRIMARY KEY,
                date       TEXT NOT NULL,
                master     TEXT NOT NULL,
                master_key TEXT DEFAULT 'default',
                start_time TEXT NOT NULL,
                end_time   TEXT NOT NULL,
                reason     TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
    else:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS unavailable_periods (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT NOT NULL,
                master     TEXT NOT NULL,
                master_key TEXT DEFAULT 'default',
                start_time TEXT NOT NULL,
                end_time   TEXT NOT NULL,
                reason     TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_unavailable_periods_date_master_key "
        "ON unavailable_periods(date, master_key, start_time, end_time)"
    )
    await conn.commit()


async def _ensure_scheduler_locks_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_locks (
            lock_name   TEXT PRIMARY KEY,
            owner       TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduler_locks_expires_at "
        "ON scheduler_locks(expires_at)"
    )
    await conn.commit()


async def _ensure_booking_slots_table(conn) -> None:
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS booking_slots (
            booking_id TEXT NOT NULL,
            date       TEXT NOT NULL,
            master_key TEXT NOT NULL,
            slot_time  TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (booking_id, slot_time)
        )
    """)
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_booking_slots_unique "
        "ON booking_slots(date, master_key, slot_time)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_booking_slots_booking_id "
        "ON booking_slots(booking_id)"
    )
    await conn.commit()


async def _backfill_booking_slots(conn) -> None:
    rows = await conn.fetch(
        "SELECT id, date, time, master_key, duration_minutes FROM bookings WHERE status='active'"
    )
    now = get_now(config.TIMEZONE).isoformat()
    for row in rows:
        master_key = normalize_master_key(row.get("master_key"))
        for slot_time in slot_times_for_range(row["time"], row.get("duration_minutes")):
            await conn.execute(
                "INSERT OR IGNORE INTO booking_slots (booking_id, date, master_key, slot_time, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                row["id"], row["date"], master_key, slot_time, now,
            )
    await conn.commit()


# ======================================================================
# init_db
# ======================================================================

async def init_db() -> None:
    if _db.is_postgres():
        await _init_pg()
    else:
        await _init_sqlite()


async def _init_pg() -> None:
    async with _db.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id          TEXT PRIMARY KEY,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                name        TEXT NOT NULL,
                telegram_id BIGINT NOT NULL,
                username    TEXT DEFAULT \'\',
                master      TEXT NOT NULL,
                master_key  TEXT DEFAULT 'default',
                service     TEXT NOT NULL,
                price       INTEGER NOT NULL,
                duration_minutes INTEGER DEFAULT 30,
                comment     TEXT DEFAULT '',
                status      TEXT DEFAULT \'active\',
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_slot "
            "ON bookings(date, time, master) WHERE status=\'active\'"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                phone       TEXT,
                username    TEXT,
                first_name  TEXT,
                blocked     INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS waitlist (
                id          SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                name        TEXT NOT NULL,
                master      TEXT NOT NULL,
                master_key  TEXT DEFAULT 'default',
                service     TEXT NOT NULL,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                duration_minutes INTEGER DEFAULT 30,
                status      TEXT DEFAULT \'waiting\',
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS loyalty (
                telegram_id BIGINT PRIMARY KEY,
                name        TEXT,
                visits      INTEGER DEFAULT 0,
                bonuses     INTEGER DEFAULT 0,
                ref_code    TEXT UNIQUE,
                updated_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id          SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_id BIGINT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id          SERIAL PRIMARY KEY,
                booking_id  TEXT NOT NULL,
                telegram_id BIGINT NOT NULL,
                rating      INTEGER NOT NULL,
                comment     TEXT DEFAULT \'\',
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS services (
                name  TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                duration_minutes INTEGER DEFAULT 30
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduler_jobs (
                id         TEXT PRIMARY KEY,
                run_date   TEXT NOT NULL,
                job_type   TEXT NOT NULL,
                booking_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS slot_locks (
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                master      TEXT NOT NULL,
                master_key  TEXT DEFAULT 'default',
                owner_id    BIGINT,
                owner_token TEXT DEFAULT '',
                locked_at   TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                PRIMARY KEY (date, time, master_key)
            )
        """)
        await _ensure_booking_slots_table(conn)
        await _ensure_scheduler_locks_table(conn)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_photos (
                id         SERIAL PRIMARY KEY,
                file_id    TEXT NOT NULL,
                caption    TEXT DEFAULT \'\',
                created_at TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS social_links (
                id         SERIAL PRIMARY KEY,
                platform   TEXT NOT NULL,
                url        TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id          SERIAL PRIMARY KEY,
                admin_id    BIGINT NOT NULL,
                action      TEXT NOT NULL,
                entity_type TEXT DEFAULT '',
                entity_id   TEXT DEFAULT '',
                old_value   TEXT DEFAULT '',
                new_value   TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_date_master ON bookings(date, master, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_date_master_key ON bookings(date, master_key, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_telegram ON bookings(telegram_id, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_waitlist_slot ON waitlist(date, time, master, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_loyalty_telegram ON loyalty(telegram_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_booking ON reviews(booking_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_created_at ON admin_audit_log(created_at)")
        await _ensure_unavailable_periods_table(conn)
        # Migrate existing schema (no-op on fresh DB)
        await _migrate_bookings_bonus_spent(conn)
        await _migrate_bookings_comment(conn)
        await _migrate_users_blocked(conn)
        await _migrate_duration_columns(conn)
        await _migrate_master_key_columns(conn)
        await _migrate_slot_lock_owner_columns(conn)
        await _ensure_waitlist_unique_index(conn)
        await _backfill_booking_slots(conn)


async def _init_sqlite() -> None:
    import aiosqlite
    db_path = Path(config.DB_PATH)
    db_dir = db_path.parent
    db_dir.mkdir(parents=True, exist_ok=True)

    can_write = os.access(str(db_dir), os.W_OK)
    logger.info(f"DB path: {db_path} | dir writable={can_write}")
    if not can_write:
        raise PermissionError(f"No write access to database directory: {db_dir}")

    async with _db.acquire() as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id          TEXT PRIMARY KEY,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                name        TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                username    TEXT DEFAULT \'\',
                master      TEXT NOT NULL,
                master_key  TEXT DEFAULT 'default',
                service     TEXT NOT NULL,
                price       INTEGER NOT NULL,
                duration_minutes INTEGER DEFAULT 30,
                comment     TEXT DEFAULT '',
                status      TEXT DEFAULT \'active\',
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_slot "
            "ON bookings(date, time, master) WHERE status=\'active\'"
        )
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                phone       TEXT,
                username    TEXT,
                first_name  TEXT,
                blocked     INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS waitlist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                name        TEXT NOT NULL,
                master      TEXT NOT NULL,
                master_key  TEXT DEFAULT 'default',
                service     TEXT NOT NULL,
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                duration_minutes INTEGER DEFAULT 30,
                status      TEXT DEFAULT \'waiting\',
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS loyalty (
                telegram_id INTEGER PRIMARY KEY,
                name        TEXT,
                visits      INTEGER DEFAULT 0,
                bonuses     INTEGER DEFAULT 0,
                ref_code    TEXT UNIQUE,
                updated_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id  TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                rating      INTEGER NOT NULL,
                comment     TEXT DEFAULT \'\',
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS services (
                name  TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                duration_minutes INTEGER DEFAULT 30
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduler_jobs (
                id         TEXT PRIMARY KEY,
                run_date   TEXT NOT NULL,
                job_type   TEXT NOT NULL,
                booking_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS slot_locks (
                date        TEXT NOT NULL,
                time        TEXT NOT NULL,
                master      TEXT NOT NULL,
                master_key  TEXT DEFAULT 'default',
                owner_id    INTEGER,
                owner_token TEXT DEFAULT '',
                locked_at   TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                PRIMARY KEY (date, time, master_key)
            )
        """)
        await _ensure_booking_slots_table(conn)
        await _ensure_scheduler_locks_table(conn)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_photos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id    TEXT NOT NULL,
                caption    TEXT DEFAULT \'\',
                created_at TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS social_links (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                platform   TEXT NOT NULL,
                url        TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id    INTEGER NOT NULL,
                action      TEXT NOT NULL,
                entity_type TEXT DEFAULT '',
                entity_id   TEXT DEFAULT '',
                old_value   TEXT DEFAULT '',
                new_value   TEXT DEFAULT '',
                created_at  TEXT NOT NULL
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_date_master ON bookings(date, master, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_date_master_key ON bookings(date, master_key, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_telegram ON bookings(telegram_id, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_created_at ON bookings(created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_status_date ON bookings(status, date)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_waitlist_slot ON waitlist(date, time, master, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_waitlist_telegram ON waitlist(telegram_id, status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_loyalty_telegram ON loyalty(telegram_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_booking ON reviews(booking_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_created_at ON admin_audit_log(created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_booking ON scheduler_jobs(booking_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_created_at ON portfolio_photos(created_at)")
        await _ensure_unavailable_periods_table(conn)
        await conn.commit()
        await _migrate_bookings_bonus_spent(conn)
        await _migrate_bookings_comment(conn)
        await _migrate_users_blocked(conn)
        await _migrate_duration_columns(conn)
        await _migrate_master_key_columns(conn)
        await _migrate_slot_lock_owner_columns(conn)
        await _ensure_waitlist_unique_index(conn)
        await _backfill_booking_slots(conn)


# ======================================================================
# Users
# ======================================================================

async def save_user(telegram_id: int, phone: str = "", username: str = "", first_name: str = ""):
    now = get_now(config.TIMEZONE).isoformat()
    async with _db.acquire() as conn:
        if _db.is_postgres():
            await conn.execute(
                "INSERT INTO users (telegram_id, phone, username, first_name, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (telegram_id) DO NOTHING",
                telegram_id, phone or "", username, first_name, now,
            )
            if phone:
                await conn.execute(
                    "UPDATE users SET phone=?, username=?, first_name=? WHERE telegram_id=?",
                    phone, username, first_name, telegram_id,
                )
            else:
                await conn.execute(
                    "UPDATE users SET username=?, first_name=? WHERE telegram_id=?",
                    username, first_name, telegram_id,
                )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO users (telegram_id, phone, username, first_name, created_at) VALUES (?, ?, ?, ?, ?)",
                telegram_id, phone or "", username, first_name, now,
            )
            if phone:
                await conn.execute("UPDATE users SET phone=?, username=?, first_name=? WHERE telegram_id=?",
                                   phone, username, first_name, telegram_id)
            else:
                await conn.execute("UPDATE users SET username=?, first_name=? WHERE telegram_id=?",
                                   username, first_name, telegram_id)
            await conn.commit()


async def get_user(telegram_id: int) -> dict | None:
    async with _db.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE telegram_id=?", telegram_id)


async def get_all_users() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch("SELECT * FROM users ORDER BY created_at")


async def is_user_blocked(telegram_id: int) -> bool:
    async with _db.acquire() as conn:
        blocked = await conn.fetchval("SELECT blocked FROM users WHERE telegram_id=?", telegram_id)
        return bool(blocked or 0)


async def set_user_blocked(telegram_id: int, blocked: bool) -> None:
    async with _db.acquire() as conn:
        await conn.execute("UPDATE users SET blocked=? WHERE telegram_id=?", 1 if blocked else 0, telegram_id)
        await conn.commit()
    # HIGH-04: When blocking a user, cancel all their active bookings to free slots
    if blocked:
        try:
            active_bookings = await get_user_bookings(telegram_id)
            for booking in active_bookings:
                await cancel_booking(booking["id"], telegram_id=telegram_id)
                # Cancel reminders for this booking
                try:
                    from scheduler import cancel_reminders
                    await cancel_reminders(booking["id"])
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Failed to cancel bookings for blocked user {telegram_id}: {e}")


# ======================================================================
# Bookings
# ======================================================================

async def _has_active_booking_overlap(
    conn,
    date: str,
    start_time: str,
    master: str,
    duration_minutes: int,
    master_key: str | None = None,
) -> bool:
    key = normalize_master_key(master_key)
    requested_slots = slot_times_for_range(start_time, duration_minutes)
    if not requested_slots:
        return True
    placeholders = ",".join("?" for _ in requested_slots)
    existing_slot = await conn.fetchval(
        f"SELECT 1 FROM booking_slots WHERE date=? AND master_key=? AND slot_time IN ({placeholders}) LIMIT 1",
        date,
        key,
        *requested_slots,
    )
    if existing_slot:
        return True

    # Fallback for databases that have not completed booking_slots backfill yet.
    rows = await conn.fetch(
        "SELECT time, duration_minutes FROM bookings WHERE date=? AND master_key=? AND status='active'",
        date,
        key,
    )
    return any(
        time_ranges_overlap(start_time, duration_minutes, row["time"], row.get("duration_minutes"))
        for row in rows
    )


async def _has_unavailable_period_overlap(
    conn,
    date: str,
    start_time: str,
    master: str,
    duration_minutes: int,
    master_key: str | None = None,
) -> bool:
    rows = await conn.fetch(
        "SELECT start_time, end_time FROM unavailable_periods WHERE date=? AND master_key=?",
        date,
        normalize_master_key(master_key),
    )
    return any(_period_overlaps(start_time, duration_minutes, row) for row in rows)


async def is_time_range_available(date: str, start_time: str, master: str, duration_minutes: int | None = None) -> bool:
    duration = normalize_duration_minutes(duration_minutes)
    master_key = normalize_master_key()
    async with _db.acquire() as conn:
        if not booking_range_fits_working_day(date, start_time, duration):
            return False
        if not booking_time_is_far_enough(date, start_time):
            return False
        if await _has_active_booking_overlap(conn, date, start_time, master, duration, master_key):
            return False
        if await _has_unavailable_period_overlap(conn, date, start_time, master, duration, master_key):
            return False
        return True


async def _apply_discounts_in_transaction(conn, telegram_id: int, base_price: int) -> tuple[int, str, int]:
    final_price = int(base_price)
    info_parts: list[str] = []
    row = await conn.fetchrow("SELECT visits, bonuses FROM loyalty WHERE telegram_id=?", telegram_id)
    if not row:
        return max(0, final_price), "", 0

    visits = row.get("visits", 0) or 0
    bonuses = row.get("bonuses", 0) or 0
    if visits > 0 and visits % config.LOYALTY_VISIT_INTERVAL == 0:
        discount_amount = int(base_price * config.LOYALTY_DISCOUNT_PERCENT / 100)
        final_price -= discount_amount
        info_parts.append(
            f"⭐ Скидка лояльности {config.LOYALTY_DISCOUNT_PERCENT}% — −{discount_amount:,} ₸".replace(",", " ")
        )

    bonus_spend = 0
    if bonuses > 0:
        max_bonus_spend = max(0, final_price // 2)
        bonus_spend = min(bonuses, max_bonus_spend)
        if bonus_spend > 0:
            updated = await conn.execute_count(
                "UPDATE loyalty SET bonuses=bonuses-?, updated_at=? WHERE telegram_id=? AND bonuses>=?",
                bonus_spend,
                get_now(config.TIMEZONE).isoformat(),
                telegram_id,
                bonus_spend,
            )
            if updated != 1:
                raise _BookingRejected("bonus balance changed during booking")
            final_price -= bonus_spend
            info_parts.append(
                f"🎁 Бонусы списаны — −{bonus_spend:,} ₸ (осталось: {bonuses - bonus_spend})".replace(",", " ")
            )

    return max(0, final_price), "".join(info_parts), bonus_spend


async def _lock_user_booking_limit(conn, telegram_id: int) -> None:
    if _db.is_postgres():
        await conn.fetchval("SELECT pg_advisory_xact_lock(?)", int(telegram_id))


async def _active_future_booking_count(conn, telegram_id: int) -> int:
    today = get_now(config.TIMEZONE).strftime("%Y-%m-%d")
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM bookings WHERE telegram_id=? AND status='active' AND date >= ?",
        int(telegram_id), today,
    )
    return int(count or 0)


async def save_booking(booking: dict) -> str | None:
    duration_minutes = normalize_duration_minutes(
        booking.get("duration_minutes") or config.get_service_duration(booking.get("service", ""))
    )
    requested_slots = slot_times_for_range(booking["time"], duration_minutes)
    master_key = normalize_master_key(booking.get("master_key"))
    try:
        async with _db.acquire() as conn:
            async with conn.transaction():
                now = get_now(config.TIMEZONE).isoformat()
                telegram_id = int(booking["telegram_id"])

                await _lock_user_booking_limit(conn, telegram_id)
                if await _active_future_booking_count(conn, telegram_id) >= config.MAX_ACTIVE_BOOKINGS:
                    raise _BookingRejected("active booking limit reached")

                if not booking_range_fits_working_day(booking["date"], booking["time"], duration_minutes):
                    raise _BookingRejected("booking range is outside working hours")
                if not booking_time_is_far_enough(booking["date"], booking["time"]):
                    raise _BookingRejected("booking time is too soon or in the past")
                # HIGH-01 TOCTOU fix: lock overlapping booking_slots rows before checking
                requested_slots_check = slot_times_for_range(booking["time"], duration_minutes)
                if requested_slots_check:
                    placeholders = ",".join("?" for _ in requested_slots_check)
                    if _db.is_postgres():
                        # PG: lock rows with FOR UPDATE to prevent concurrent inserts
                        await conn.execute(
                            f"SELECT 1 FROM booking_slots WHERE date=? AND master_key=? "
                            f"AND slot_time IN ({placeholders}) FOR UPDATE",
                            booking["date"], master_key, *requested_slots_check,
                        )
                    else:
                        # SQLite: BEGIN EXCLUSIVE already locks the DB, but we also
                        # do a manual check with INSERT OR FAIL pattern below
                        pass

                if await _has_active_booking_overlap(
                    conn, booking["date"], booking["time"], booking["master"], duration_minutes, master_key
                ):
                    raise _BookingRejected("booking range overlaps active booking")
                if await _has_unavailable_period_overlap(
                    conn, booking["date"], booking["time"], booking["master"], duration_minutes, master_key
                ):
                    raise _BookingRejected("booking range overlaps unavailable period")

                final_price = int(booking["price"])
                bonus_spent = int(booking.get("bonus_spent", 0) or 0)
                discount_info = booking.get("discount_info", "") or ""
                if booking.get("apply_discounts"):
                    final_price, discount_info, bonus_spent = await _apply_discounts_in_transaction(
                        conn,
                        telegram_id,
                        int(booking["price"]),
                    )

                for attempt in range(3):
                    booking_id = uuid.uuid4().hex[:12]
                    try:
                        await conn.execute(
                            "INSERT INTO bookings (id, date, time, name, telegram_id, username, "
                            "master, master_key, service, price, duration_minutes, comment, status, bonus_spent, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                            booking_id, booking["date"], booking["time"], booking["name"],
                            booking["telegram_id"], booking.get("username", ""),
                            booking["master"], master_key, booking["service"], final_price,
                            duration_minutes, booking.get("comment", ""), bonus_spent, now,
                        )
                        for slot_time in requested_slots:
                            await conn.execute(
                                "INSERT INTO booking_slots (booking_id, date, master_key, slot_time, created_at) "
                                "VALUES (?, ?, ?, ?, ?)",
                                booking_id, booking["date"], master_key, slot_time, now,
                            )
                        for lock_time in requested_slots:
                            await conn.execute(
                                "DELETE FROM slot_locks WHERE date=? AND time=? AND master_key=?",
                                booking["date"], lock_time, master_key,
                            )

                        booking["price"] = final_price
                        booking["duration_minutes"] = duration_minutes
                        booking["bonus_spent"] = bonus_spent
                        booking["discount_info"] = discount_info
                        booking["master_key"] = master_key
                        _increment_metric("bookings_created")
                        _log_event(
                            "booking_created",
                            booking_id=booking_id,
                            date=booking["date"],
                            time=booking["time"],
                            service=booking["service"],
                            price=final_price,
                        )
                        return booking_id
                    except Exception as e:
                        if _is_unique_constraint_error(e) and _is_primary_key_conflict(e) and attempt < 2:
                            continue
                        if _is_unique_constraint_error(e):
                            logger.info(
                                "Booking slot conflict: %s %s %s",
                                booking["date"], booking["time"], master_key,
                            )
                            raise _BookingRejected("booking slot conflict") from e
                        raise
                raise _BookingRejected("booking id generation failed")
    except _BookingRejected as e:
        logger.info("Booking rejected: %s", e)
        return None


async def get_booked_slots(date: str, master: str) -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT time, duration_minutes FROM bookings WHERE date=? AND master_key=? AND status='active'",
            date, normalize_master_key(),
        )


async def get_user_bookings(telegram_id: int) -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM bookings WHERE telegram_id=? AND status='active' ORDER BY date, time",
            telegram_id,
        )


async def cancel_booking(booking_id: str, telegram_id: int = None) -> dict | None:
    async with _db.acquire() as conn:
        async with conn.transaction():
            if telegram_id:
                row = await conn.fetchrow(
                    "SELECT * FROM bookings WHERE id=? AND status='active' AND telegram_id=?",
                    booking_id, telegram_id,
                )
            else:
                row = await conn.fetchrow(
                    "SELECT * FROM bookings WHERE id=? AND status='active'", booking_id
                )
            if not row:
                return None
            await conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", booking_id)
            await conn.execute("DELETE FROM booking_slots WHERE booking_id=?", booking_id)
            _increment_metric("bookings_cancelled")
            _log_event("booking_cancelled", booking_id=booking_id, source="client")
            for lock_time in slot_times_for_range(row["time"], row.get("duration_minutes")):
                await conn.execute(
                    "DELETE FROM slot_locks WHERE date=? AND time=? AND master_key=?",
                    row["date"], lock_time, normalize_master_key(row.get("master_key")),
                )
            return row


async def complete_booking(booking_id: str) -> dict | None:
    async with _db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM bookings WHERE id=? AND status='active'", booking_id
            )
            if not row:
                return None
            await conn.execute("UPDATE bookings SET status='completed' WHERE id=?", booking_id)
            await conn.execute("DELETE FROM booking_slots WHERE booking_id=?", booking_id)
            return row


async def admin_cancel_booking(booking_id: str) -> dict | None:
    async with _db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM bookings WHERE id=? AND status='active'", booking_id
            )
            if not row:
                return None
            await conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", booking_id)
            await conn.execute("DELETE FROM booking_slots WHERE booking_id=?", booking_id)
            _increment_metric("bookings_cancelled")
            _log_event("booking_cancelled", booking_id=booking_id, source="admin")
            for lock_time in slot_times_for_range(row["time"], row.get("duration_minutes")):
                await conn.execute(
                    "DELETE FROM slot_locks WHERE date=? AND time=? AND master_key=?",
                    row["date"], lock_time, normalize_master_key(row.get("master_key")),
                )
            return row


async def admin_complete_booking(booking_id: str) -> dict | None:
    async with _db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM bookings WHERE id=? AND status='active'", booking_id
            )
            if not row:
                return None
            await conn.execute("UPDATE bookings SET status='completed' WHERE id=?", booking_id)
            await conn.execute("DELETE FROM booking_slots WHERE booking_id=?", booking_id)
            return row


async def get_upcoming_bookings() -> list[dict]:
    now = get_now(config.TIMEZONE)
    today = now.strftime("%Y-%m-%d")
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM bookings WHERE status='active' "
            "AND (date>? OR (date=? AND time>=?)) ORDER BY date, time",
            today, today, now.strftime("%H:%M"),
        )

async def get_upcoming_bookings_paged(offset: int = 0, limit: int = 5):
    """HIGH-003 FIX: SQL-level pagination - no full table scan in Python.
    Returns (page_items: list[dict], total: int).
    """
    now = get_now(config.TIMEZONE)
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    async with _db.acquire() as conn:
        if _db.is_postgres():
            total = (await conn.fetchval(
                "SELECT COUNT(*) FROM bookings WHERE status='active'"
                " AND (date>$1 OR (date=$1 AND time>=$2))",
                today, current_time,
            )) or 0
            rows = await conn.fetch(
                "SELECT * FROM bookings WHERE status='active'"
                " AND (date>$1 OR (date=$1 AND time>=$2)) ORDER BY date, time LIMIT $3 OFFSET $4",
                today, current_time, limit, offset,
            )
        else:
            total = (await conn.fetchval(
                "SELECT COUNT(*) FROM bookings WHERE status='active'"
                " AND (date>? OR (date=? AND time>=?))",
                today, today, current_time,
            )) or 0
            rows = await conn.fetch(
                "SELECT * FROM bookings WHERE status='active'"
                " AND (date>? OR (date=? AND time>=?)) ORDER BY date, time LIMIT ? OFFSET ?",
                today, today, current_time, limit, offset,
            )
        return list(rows), int(total)



async def get_past_bookings_for_completion() -> list[dict]:
    now = get_now(config.TIMEZONE)
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM bookings WHERE status='active' "
            "AND (date<? OR (date=? AND time<?)) ORDER BY date, time",
            today, today, current_time,
        )


async def get_all_bookings() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch("SELECT * FROM bookings ORDER BY date, time")


async def export_bookings_csv() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT id, date, time, master, service, price, duration_minutes, "
            "status, bonus_spent, created_at FROM bookings ORDER BY date, time"
        )


async def cleanup_old_bookings(days: int = 90) -> int:
    result = await apply_retention_policy(booking_days=days)
    return int(result.get("bookings_anonymized", 0))


async def export_client_data(telegram_id: int) -> dict:
    async with _db.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=?", telegram_id)
        bookings = await conn.fetch(
            "SELECT * FROM bookings WHERE telegram_id=? ORDER BY date, time",
            telegram_id,
        )
        waitlist = await conn.fetch(
            "SELECT * FROM waitlist WHERE telegram_id=? ORDER BY created_at",
            telegram_id,
        )
        loyalty = await conn.fetchrow("SELECT * FROM loyalty WHERE telegram_id=?", telegram_id)
        reviews = await conn.fetch(
            "SELECT * FROM reviews WHERE telegram_id=? ORDER BY created_at DESC",
            telegram_id,
        )
        referrals_made = (await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id=?",
            telegram_id,
        )) or 0
        referrals_received = (await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referred_id=?",
            telegram_id,
        )) or 0
    return {
        "telegram_id": telegram_id,
        "user": user,
        "bookings": bookings,
        "waitlist": waitlist,
        "loyalty": loyalty,
        "reviews": reviews,
        "referral_counts": {
            "as_referrer": referrals_made,
            "as_referred": referrals_received,
        },
    }


async def anonymize_client_data(telegram_id: int) -> dict:
    if telegram_id <= 0:
        raise ValueError("telegram_id must be positive")
    async with _db.acquire() as conn:
        return {
            "users_deleted": await conn.execute_count(
                "DELETE FROM users WHERE telegram_id=?",
                telegram_id,
            ),
            "bookings_anonymized": await conn.execute_count(
                "UPDATE bookings SET telegram_id=0, name=?, username='', comment='' WHERE telegram_id=?",
                "Удаленный клиент",
                telegram_id,
            ),
            "waitlist_anonymized": await conn.execute_count(
                "UPDATE waitlist SET telegram_id=0, name=?, status='privacy_deleted' WHERE telegram_id=?",
                "Удаленный клиент",
                telegram_id,
            ),
            "loyalty_deleted": await conn.execute_count(
                "DELETE FROM loyalty WHERE telegram_id=?",
                telegram_id,
            ),
            "reviews_anonymized": await conn.execute_count(
                "UPDATE reviews SET telegram_id=0, comment='' WHERE telegram_id=?",
                telegram_id,
            ),
            "referrals_as_referrer_anonymized": await conn.execute_count(
                "UPDATE referrals SET referrer_id=0 WHERE referrer_id=?",
                telegram_id,
            ),
            "referrals_as_referred_anonymized": await conn.execute_count(
                "UPDATE referrals SET referred_id=0 WHERE referred_id=?",
                telegram_id,
            ),
        }


async def apply_retention_policy(
    booking_days: int | None = None,
    audit_days: int | None = None,
) -> dict:
    booking_days = int(booking_days or config.PRIVACY_RETENTION_DAYS)
    audit_days = int(audit_days or config.ADMIN_AUDIT_RETENTION_DAYS)
    cutoff_date = (get_now(config.TIMEZONE) - timedelta(days=booking_days)).strftime("%Y-%m-%d")
    cutoff_audit = (get_now(config.TIMEZONE) - timedelta(days=audit_days)).isoformat()
    async with _db.acquire() as conn:
        reviews_anonymized = await conn.execute_count(
            "UPDATE reviews SET telegram_id=0, comment='' WHERE booking_id IN "
            "(SELECT id FROM bookings WHERE date < ? AND status IN ('cancelled', 'completed')) "
            "AND telegram_id != 0",
            cutoff_date,
        )
        bookings_anonymized = await conn.execute_count(
            "UPDATE bookings SET telegram_id=0, name=?, username='', comment='' "
            "WHERE date < ? AND status IN ('cancelled', 'completed') AND telegram_id != 0",
            "Удаленный клиент",
            cutoff_date,
        )
        audit_deleted = await conn.execute_count(
            "DELETE FROM admin_audit_log WHERE created_at < ?",
            cutoff_audit,
        )
        waitlist_anonymized = await conn.execute_count(
            "UPDATE waitlist SET telegram_id=0, name=?, status='privacy_deleted' "
            "WHERE created_at < ? AND status != 'waiting' AND telegram_id != 0",
            "Удаленный клиент",
            cutoff_audit,
        )
        return {
            "bookings_anonymized": bookings_anonymized,
            "reviews_anonymized": reviews_anonymized,
            "admin_audit_deleted": audit_deleted,
            "waitlist_anonymized": waitlist_anonymized,
        }


async def has_active_booking(telegram_id: int) -> bool:
    async with _db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM bookings WHERE telegram_id=? AND status='active' AND date >= ?",
            telegram_id, get_now(config.TIMEZONE).strftime("%Y-%m-%d"),
        )
        return (count or 0) >= config.MAX_ACTIVE_BOOKINGS


async def user_rate_limit_check(telegram_id: int, window: int = 3600, max_attempts: int = 3) -> bool:
    since = (get_now(config.TIMEZONE) - timedelta(seconds=window)).isoformat()
    async with _db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM bookings WHERE telegram_id=? AND created_at>=?",
            telegram_id, since,
        )
        return (count or 0) < max_attempts


async def get_booking_with_user(booking_id: str) -> dict | None:
    async with _db.acquire() as conn:
        return await conn.fetchrow(
            "SELECT b.*, u.phone, u.username as user_username, u.blocked as user_blocked FROM bookings b "
            "LEFT JOIN users u ON b.telegram_id = u.telegram_id WHERE b.id=?",
            booking_id,
        )


async def get_bookings_by_date_range(start_date: str, end_date: str) -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM bookings WHERE date BETWEEN ? AND ? ORDER BY date, time",
            start_date, end_date,
        )


# ======================================================================
# Waitlist
# ======================================================================

async def _has_active_slot_lock_overlap(
    conn,
    date: str,
    start_time: str,
    master_key: str,
    duration_minutes: int,
) -> bool:
    requested_slots = slot_times_for_range(start_time, duration_minutes)
    if not requested_slots:
        return False
    placeholders = ",".join("?" for _ in requested_slots)
    row = await conn.fetchval(
        f"SELECT 1 FROM slot_locks WHERE date=? AND master_key=? AND time IN ({placeholders}) "
        "AND expires_at > ? LIMIT 1",
        date,
        master_key,
        *requested_slots,
        get_now(config.TIMEZONE).isoformat(),
    )
    return bool(row)


async def _waitlist_slot_is_eligible(
    conn,
    date: str,
    time: str,
    master: str,
    duration_minutes: int,
    master_key: str,
) -> bool:
    if not booking_range_fits_working_day(date, time, duration_minutes):
        return False
    if not booking_time_is_far_enough(date, time):
        return False
    if await _has_active_booking_overlap(conn, date, time, master, duration_minutes, master_key):
        return True
    if await _has_unavailable_period_overlap(conn, date, time, master, duration_minutes, master_key):
        return True
    return await _has_active_slot_lock_overlap(conn, date, time, master_key, duration_minutes)


async def add_to_waitlist(
    telegram_id: int,
    name: str,
    master: str,
    service: str,
    date: str,
    time: str,
    duration_minutes: int | None = None,
) -> bool:
    now = get_now(config.TIMEZONE).isoformat()
    duration = normalize_duration_minutes(duration_minutes or config.get_service_duration(service))
    master_key = normalize_master_key()
    try:
        async with _db.acquire() as conn:
            async with conn.transaction():
                if not await _waitlist_slot_is_eligible(conn, date, time, master, duration, master_key):
                    return False
                if _db.is_postgres():
                    inserted = await conn.execute_count(
                        "INSERT INTO waitlist "
                        "(telegram_id, name, master, master_key, service, date, time, duration_minutes, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?) ON CONFLICT DO NOTHING",
                        telegram_id, name, master, master_key, service, date, time, duration, now,
                    )
                else:
                    inserted = await conn.execute_count(
                        "INSERT OR IGNORE INTO waitlist "
                        "(telegram_id, name, master, master_key, service, date, time, duration_minutes, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?)",
                        telegram_id, name, master, master_key, service, date, time, duration, now,
                    )
                return inserted == 1
    except Exception as e:
        if _is_unique_constraint_error(e):
            return False
        logger.warning(f"Failed to add waitlist entry {telegram_id}/{date}/{time}: {e}")
        return False


async def get_waitlist_for_slot(date: str, time: str, master: str) -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM waitlist WHERE date=? AND time=? AND master_key=? AND status='waiting' ORDER BY id",
            date, time, normalize_master_key(),
        )


async def get_waitlist_for_open_period(date: str, master: str, start_time: str, duration_minutes: int | None = None) -> list[dict]:
    freed_duration = normalize_duration_minutes(duration_minutes)
    master_key = normalize_master_key()
    async with _db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM waitlist WHERE date=? AND master_key=? AND status='waiting' ORDER BY id",
            date, master_key,
        )
        result = []
        for row in rows:
            row_duration = normalize_duration_minutes(row.get("duration_minutes") or config.get_service_duration(row.get("service", "")))
            if not time_ranges_overlap(row["time"], row_duration, start_time, freed_duration):
                continue
            if await _has_active_booking_overlap(conn, date, row["time"], master, row_duration, master_key):
                continue
            if await _has_unavailable_period_overlap(conn, date, row["time"], master, row_duration, master_key):
                continue
            result.append(row)
        return result


async def update_waitlist_status(waitlist_id: int, status: str):
    async with _db.acquire() as conn:
        await conn.execute("UPDATE waitlist SET status=? WHERE id=?", status, waitlist_id)
        await conn.commit()


async def get_all_waitlist() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch("SELECT * FROM waitlist ORDER BY created_at")


async def get_all_waitlist_paged(offset: int = 0, limit: int = 15):
    """L-7 FIX: SQL-level pagination for admin waitlist page.
    Returns (page_items: list[dict], total: int).
    """
    async with _db.acquire() as conn:
        if _db.is_postgres():
            total = (await conn.fetchval("SELECT COUNT(*) FROM waitlist")) or 0
            rows = await conn.fetch("SELECT * FROM waitlist ORDER BY created_at LIMIT $1 OFFSET $2", limit, offset)
        else:
            total = (await conn.fetchval("SELECT COUNT(*) FROM waitlist")) or 0
            rows = await conn.fetch("SELECT * FROM waitlist ORDER BY created_at LIMIT ? OFFSET ?", limit, offset)
        return list(rows), int(total)

async def get_user_waitlist(telegram_id: int) -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM waitlist WHERE telegram_id=? AND status='waiting' ORDER BY created_at",
            telegram_id,
        )


async def get_user_waitlist_count(telegram_id: int) -> int:
    async with _db.acquire() as conn:
        return (await conn.fetchval(
            "SELECT COUNT(*) FROM waitlist WHERE telegram_id=? AND status='waiting'",
            telegram_id,
        )) or 0


# ======================================================================
# Loyalty
# ======================================================================

async def update_loyalty(telegram_id: int, name: str = "") -> int:
    async with _db.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT visits FROM loyalty WHERE telegram_id=?", telegram_id
            )
            now = get_now(config.TIMEZONE).isoformat()
            if row:
                visits = row["visits"] + 1
                await conn.execute(
                    "UPDATE loyalty SET visits=?, updated_at=? WHERE telegram_id=?",
                    visits, now, telegram_id,
                )
            else:
                visits = 1
                ref_code = uuid.uuid4().hex[:8]
                if _db.is_postgres():
                    await conn.execute(
                        "INSERT INTO loyalty (telegram_id, name, visits, bonuses, ref_code, updated_at) "
                        "VALUES (?, ?, ?, 0, ?, ?) ON CONFLICT DO NOTHING",
                        telegram_id, name, visits, ref_code, now,
                    )
                else:
                    await conn.execute(
                        "INSERT OR IGNORE INTO loyalty (telegram_id, name, visits, bonuses, ref_code, updated_at) "
                        "VALUES (?, ?, ?, 0, ?, ?)",
                        telegram_id, name, visits, ref_code, now,
                    )
            return visits


async def add_bonus(telegram_id: int, amount: int) -> bool:
    async with _db.acquire() as conn:
        exists = await conn.fetchval("SELECT telegram_id FROM loyalty WHERE telegram_id=?", telegram_id)
        if not exists:
            return False
        await conn.execute(
            "UPDATE loyalty SET bonuses=bonuses+?, updated_at=? WHERE telegram_id=?",
            amount, get_now(config.TIMEZONE).isoformat(), telegram_id,
        )
        await conn.commit()
        return True






async def spend_bonus(telegram_id: int, amount: int) -> bool:

    """CRIT-002 FIX: Deduct bonuses from user loyalty balance.

    Returns False if user has insufficient bonuses."""

    async with _db.acquire() as conn:

        row = await conn.fetchrow("SELECT bonuses FROM loyalty WHERE telegram_id=?", telegram_id)

        if not row or (row["bonuses"] or 0) < amount:

            return False

        await conn.execute(

            "UPDATE loyalty SET bonuses=bonuses-?, updated_at=? WHERE telegram_id=?",

            amount, get_now(config.TIMEZONE).isoformat(), telegram_id,

        )

        await conn.commit()

        return True

async def get_loyalty(telegram_id: int) -> dict | None:
    async with _db.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM loyalty WHERE telegram_id=?", telegram_id)


async def get_loyalty_list() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch("SELECT * FROM loyalty ORDER BY visits DESC")



async def ensure_user_ref_code(telegram_id: int, name: str = "") -> str:
    """Ensure the user has a loyalty record with a ref_code.
    Creates one if it doesn't exist yet (REFERRAL FIX: ref_code available
    to all users, not just those who completed a booking).
    Returns the ref_code.
    """
    import uuid as _uuid
    async with _db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ref_code FROM loyalty WHERE telegram_id=?", telegram_id
        )
        if row and row["ref_code"]:
            return row["ref_code"]
        # Generate a new unique ref_code
        now = get_now(config.TIMEZONE).isoformat()
        ref_code = _uuid.uuid4().hex[:8]
        if _db.is_postgres():
            await conn.execute(
                "INSERT INTO loyalty (telegram_id, name, visits, bonuses, ref_code, updated_at) "
                "VALUES (?, ?, 0, 0, ?, ?) ON CONFLICT (telegram_id) DO UPDATE SET ref_code=? WHERE loyalty.ref_code IS NULL",
                telegram_id, name, ref_code, now, ref_code,
            )
        else:
            await conn.execute(
                "INSERT OR IGNORE INTO loyalty (telegram_id, name, visits, bonuses, ref_code, updated_at) "
                "VALUES (?, ?, 0, 0, ?, ?)",
                telegram_id, name, ref_code, now,
            )
            # If record existed but had no ref_code, set it
            await conn.execute(
                "UPDATE loyalty SET ref_code=? WHERE telegram_id=? AND (ref_code IS NULL OR ref_code='')",
                ref_code, telegram_id,
            )
            await conn.commit()
        # Re-read to get actual value (race-safe)
        actual = await conn.fetchval("SELECT ref_code FROM loyalty WHERE telegram_id=?", telegram_id)
        return actual or ref_code


async def get_referral_count(telegram_id: int) -> int:
    """Return how many users registered via this user's referral link."""
    async with _db.acquire() as conn:
        return (await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id=?", telegram_id
        )) or 0

async def add_referral(referrer_id: int, referred_id: int) -> bool:
    if referrer_id == referred_id:
        return False
    async with _db.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT id FROM referrals WHERE referrer_id=? AND referred_id=?",
            referrer_id, referred_id,
        )
        if exists:
            return False
        await conn.execute(
            "INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
            referrer_id, referred_id, get_now(config.TIMEZONE).isoformat(),
        )
        await conn.commit()
        return True


async def get_referrals() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch("SELECT * FROM referrals ORDER BY created_at")


async def get_user_by_ref_code(ref_code: str) -> dict | None:
    async with _db.acquire() as conn:
        return await conn.fetchrow(
            "SELECT l.telegram_id, l.name, l.ref_code, u.phone, u.username, u.first_name "
            "FROM loyalty l LEFT JOIN users u ON l.telegram_id = u.telegram_id WHERE l.ref_code=?",
            ref_code,
        )


# ======================================================================
# Reviews
# ======================================================================

async def save_review(booking_id: str, telegram_id: int, rating: int, comment: str = "") -> bool:
    async with _db.acquire() as conn:
        status_row = await conn.fetchrow("SELECT status FROM bookings WHERE id=?", booking_id)
        if not status_row or status_row["status"] != "completed":
            return False
        dup = await conn.fetchval(
            "SELECT id FROM reviews WHERE booking_id=? AND telegram_id=?", booking_id, telegram_id
        )
        if dup:
            return False
        await conn.execute(
            "INSERT INTO reviews (booking_id, telegram_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)",
            booking_id, telegram_id, rating, comment, get_now(config.TIMEZONE).isoformat(),
        )
        await conn.commit()
        return True


async def get_reviews() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch("SELECT * FROM reviews ORDER BY created_at DESC")


# ======================================================================
# Statistics
# ======================================================================

_stats_cache: dict | None = None
_stats_cache_time: float = 0
_STATS_CACHE_TTL = 15  # seconds

async def get_stats() -> dict:
    global _stats_cache, _stats_cache_time
    import time as _t
    now = _t.time()
    if _stats_cache is not None and (now - _stats_cache_time) < _STATS_CACHE_TTL:
        return _stats_cache
    async with _db.acquire() as conn:
        total    = await conn.fetchval("SELECT COUNT(*) FROM bookings") or 0
        active   = await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE status='active'") or 0
        cancelled= await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE status='cancelled'") or 0
        completed= await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE status='completed'") or 0
        revenue  = await conn.fetchval("SELECT COALESCE(SUM(price), 0) FROM bookings WHERE status='completed'") or 0
        _stats_cache = {"total": total, "active": active, "cancelled": cancelled, "completed": completed, "revenue": revenue}
        _stats_cache_time = now
        return _stats_cache


async def get_stats_by_day() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT date, COUNT(*) as count FROM bookings "
            "WHERE status IN ('active', 'completed') GROUP BY date ORDER BY date"
        )


async def get_stats_by_service() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT service, COUNT(*) as count, SUM(price) as revenue "
            "FROM bookings WHERE status IN ('active', 'completed') GROUP BY service"
        )


async def get_service_stats(service_name: str) -> dict:
    async with _db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active, "
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed, "
            "SUM(CASE WHEN status='completed' THEN price ELSE 0 END) as revenue "
            "FROM bookings WHERE service=?",
            service_name,
        )
        return {"total": row["total"] or 0, "active": row["active"] or 0,
                "completed": row["completed"] or 0, "revenue": row["revenue"] or 0}


async def get_active_bookings_count() -> int:
    async with _db.acquire() as conn:
        return (await conn.fetchval("SELECT COUNT(*) FROM bookings WHERE status='active'")) or 0


async def get_bookings_summary(date_str: str) -> dict:
    async with _db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as total, "
            "COALESCE(SUM(CASE WHEN status='active' THEN 1 ELSE 0 END),0) as active, "
            "COALESCE(SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END),0) as cancelled, "
            "COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) as completed, "
            "COALESCE(SUM(CASE WHEN status='completed' THEN price ELSE 0 END),0) as revenue "
            "FROM bookings WHERE date=?",
            date_str,
        )
        return {"total": row["total"] or 0, "active": row["active"] or 0,
                "cancelled": row["cancelled"] or 0, "completed": row["completed"] or 0,
                "revenue": row["revenue"] or 0}


# ======================================================================
# Settings (key-value)
# ======================================================================

async def save_settings(key: str, value: str):
    global _settings_cache
    async with _db.acquire() as conn:
        await conn.upsert("settings", ["key"], {"key": key, "value": value})
    # MED-06: Invalidate cache immediately
    _settings_cache = None


async def get_settings(key: str) -> str | None:
    async with _db.acquire() as conn:
        return await conn.fetchval("SELECT value FROM settings WHERE key=?", key)


# MED-06: Simple in-memory cache for config settings
_settings_cache: dict | None = None
_settings_cache_time: float = 0
_SETTINGS_CACHE_TTL = 30  # seconds

async def get_all_settings() -> dict:
    global _settings_cache, _settings_cache_time
    import time as _t
    now = _t.time()
    if _settings_cache is not None and (now - _settings_cache_time) < _SETTINGS_CACHE_TTL:
        return _settings_cache
    async with _db.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM settings")
        _settings_cache = {r["key"]: r["value"] for r in rows}
        _settings_cache_time = now
        return _settings_cache


# ======================================================================
# Admin audit log
# ======================================================================

async def log_admin_action(
    admin_id: int,
    action: str,
    entity_type: str = "",
    entity_id: str = "",
    old_value: str = "",
    new_value: str = "",
) -> None:
    async with _db.acquire() as conn:
        await conn.execute(
            "INSERT INTO admin_audit_log "
            "(admin_id, action, entity_type, entity_id, old_value, new_value, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            admin_id,
            action,
            entity_type,
            str(entity_id or ""),
            old_value or "",
            new_value or "",
            get_now(config.TIMEZONE).isoformat(),
        )
        await conn.commit()


async def get_admin_audit_log(limit: int = 50, offset: int = 0) -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM admin_audit_log ORDER BY created_at DESC LIMIT ? OFFSET ?",
            limit,
            offset,
        )


# ======================================================================
# Services (global)
# ======================================================================

async def save_service(name: str, price: int, duration_minutes: int | None = None):
    global _services_cache
    duration = normalize_duration_minutes(duration_minutes or config.get_service_duration(name))
    async with _db.acquire() as conn:
        await conn.upsert("services", ["name"], {"name": name, "price": price, "duration_minutes": duration})
    # MED-06: Invalidate cache immediately (only after successful write)
    _services_cache = None


async def remove_service(name: str):
    global _services_cache
    async with _db.acquire() as conn:
        await conn.execute("DELETE FROM services WHERE name=?", name)
        await conn.commit()
    # MED-06: Invalidate cache immediately
    _services_cache = None


# MED-06: Simple in-memory cache for services
_services_cache: dict | None = None
_services_cache_time: float = 0
_SERVICES_CACHE_TTL = 30  # seconds

async def get_all_services() -> dict:
    global _services_cache, _services_cache_time
    import time as _t
    now = _t.time()
    if _services_cache is not None and (now - _services_cache_time) < _SERVICES_CACHE_TTL:
        return _services_cache
    async with _db.acquire() as conn:
        rows = await conn.fetch("SELECT name, price FROM services")
        _services_cache = {r["name"]: r["price"] for r in rows}
        _services_cache_time = now
        return _services_cache


async def get_all_service_durations() -> dict:
    global _services_cache
    import time as _t
    now = _t.time()
    if _services_cache is not None and (now - _services_cache_time) < _SERVICES_CACHE_TTL:
        # We cache services with prices; return durations from same cache
        pass
    async with _db.acquire() as conn:
        rows = await conn.fetch("SELECT name, duration_minutes FROM services")
        return {r["name"]: normalize_duration_minutes(r.get("duration_minutes")) for r in rows}


# ======================================================================
# Unavailable periods
# ======================================================================

def _normalize_unavailable_range(start_time: str | None, end_time: str | None) -> tuple[str, str]:
    if not start_time and not end_time:
        return "00:00", "24:00"
    if start_time and not end_time:
        return start_time, minutes_to_time(time_to_minutes(start_time) + config.SLOT_STEP_MINUTES)
    if not start_time or not end_time:
        raise ValueError("Both start_time and end_time are required for a time range")
    if time_to_minutes(end_time) <= time_to_minutes(start_time):
        raise ValueError("end_time must be later than start_time")
    return start_time, end_time


async def add_unavailable_period(
    date: str,
    start_time: str | None = None,
    end_time: str | None = None,
    master: str | None = None,
    reason: str = "",
) -> int:
    master = master or config.MASTER_NAME
    start_time, end_time = _normalize_unavailable_range(start_time, end_time)
    now = get_now(config.TIMEZONE).isoformat()
    master_key = normalize_master_key()
    async with _db.acquire() as conn:
        if _db.is_postgres():
            return await conn.fetchval(
                "INSERT INTO unavailable_periods (date, master, master_key, start_time, end_time, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                date, master, master_key, start_time, end_time, reason or "", now,
            )
        await conn.execute(
            "INSERT INTO unavailable_periods (date, master, master_key, start_time, end_time, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            date, master, master_key, start_time, end_time, reason or "", now,
        )
        await conn.commit()
        return await conn.fetchval("SELECT last_insert_rowid()")


async def add_unavailable_slot(date: str, time: str, master: str | None = None, reason: str = "") -> int:
    return await add_unavailable_period(date, time, None, master=master, reason=reason)


async def get_unavailable_periods(date: str | None = None, master: str | None = None, limit: int = 50) -> list[dict]:
    master = master or config.MASTER_NAME
    master_key = normalize_master_key()
    async with _db.acquire() as conn:
        if date:
            return await conn.fetch(
                "SELECT * FROM unavailable_periods WHERE date=? AND master_key=? ORDER BY date, start_time LIMIT ?",
                date, master_key, limit,
            )
        return await conn.fetch(
            "SELECT * FROM unavailable_periods WHERE master_key=? ORDER BY date DESC, start_time LIMIT ?",
            master_key, limit,
        )


async def get_unavailable_periods_for_date(date: str, master: str | None = None) -> list[dict]:
    return await get_unavailable_periods(date=date, master=master, limit=100)


async def delete_unavailable_period(period_id: int) -> bool:
    async with _db.acquire() as conn:
        deleted = await conn.execute_count("DELETE FROM unavailable_periods WHERE id=?", period_id)
        return deleted > 0


async def is_time_range_unavailable(date: str, start_time: str, master: str, duration_minutes: int | None = None) -> bool:
    async with _db.acquire() as conn:
        return await _has_unavailable_period_overlap(
            conn,
            date,
            start_time,
            master,
            normalize_duration_minutes(duration_minutes),
            normalize_master_key(),
        )


# ======================================================================
# Portfolio photos
# ======================================================================

async def add_portfolio_photo(file_id: str, caption: str = "") -> int:
    now = get_now(config.TIMEZONE).isoformat()
    async with _db.acquire() as conn:
        if _db.is_postgres():
            photo_id = await conn.fetchval(
                "INSERT INTO portfolio_photos (file_id, caption, created_at) VALUES (?, ?, ?) RETURNING id",
                file_id, caption, now,
            )
        else:
            await conn.execute(
                "INSERT INTO portfolio_photos (file_id, caption, created_at) VALUES (?, ?, ?)",
                file_id, caption, now,
            )
            await conn.commit()
            photo_id = await conn.fetchval("SELECT last_insert_rowid()")
        return photo_id


async def delete_portfolio_photo(photo_id: int) -> bool:
    async with _db.acquire() as conn:
        deleted = await conn.execute_count(
            "DELETE FROM portfolio_photos WHERE id=?", photo_id
        )
        return deleted > 0


async def get_portfolio_photo(photo_id: int) -> dict | None:
    async with _db.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM portfolio_photos WHERE id=?", photo_id)


async def get_portfolio_photos(limit: int = 10, offset: int = 0) -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM portfolio_photos ORDER BY created_at DESC LIMIT ? OFFSET ?",
            limit, offset,
        )


async def count_portfolio_photos() -> int:
    async with _db.acquire() as conn:
        return (await conn.fetchval("SELECT COUNT(*) FROM portfolio_photos")) or 0


# ======================================================================
# Social links
# ======================================================================

async def add_social_link(platform: str, url: str) -> int:
    now = get_now(config.TIMEZONE).isoformat()
    async with _db.acquire() as conn:
        if _db.is_postgres():
            link_id = await conn.fetchval(
                "INSERT INTO social_links (platform, url, created_at) VALUES (?, ?, ?) RETURNING id",
                platform, url, now,
            )
        else:
            await conn.execute(
                "INSERT INTO social_links (platform, url, created_at) VALUES (?, ?, ?)",
                platform, url, now,
            )
            await conn.commit()
            link_id = await conn.fetchval("SELECT last_insert_rowid()")
        return link_id


async def delete_social_link(link_id: int) -> bool:
    async with _db.acquire() as conn:
        deleted = await conn.execute_count(
            "DELETE FROM social_links WHERE id=?", link_id
        )
        return deleted > 0


async def get_social_links() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch("SELECT * FROM social_links ORDER BY created_at")


async def get_social_link_by_id(link_id: int) -> dict | None:
    async with _db.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM social_links WHERE id=?", link_id)


# ======================================================================
# Locked slots
# ======================================================================

async def get_locked_slots(date: str, master: str) -> set[str]:
    """Return set of time strings currently locked (slot_locks not expired) for given date/master."""
    try:
        now_iso = get_now(config.TIMEZONE).isoformat()
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT time FROM slot_locks WHERE date=? AND master_key=? AND expires_at > ?",
                date, normalize_master_key(), now_iso,
            )
            return {r["time"] for r in rows}
    except Exception as e:
        logger.warning(f"get_locked_slots failed: {e}")
        return set()


async def get_effective_price(master: str, service: str) -> int:
    """Return master-specific price if set, otherwise global price from config."""
    custom = await get_master_service_price(master, service)
    if custom is not None:
        return custom
    return config.SERVICES.get(service, 0)


# ======================================================================
# Scheduler jobs
# ======================================================================

async def save_scheduler_job(job_id: str, run_date: str, job_type: str, booking_id: str):
    async with _db.acquire() as conn:
        await conn.upsert(
            "scheduler_jobs",
            ["id"],
            {"id": job_id, "run_date": run_date, "job_type": job_type,
             "booking_id": booking_id, "created_at": get_now(config.TIMEZONE).isoformat()},
        )


async def remove_scheduler_job(job_id: str):
    async with _db.acquire() as conn:
        await conn.execute("DELETE FROM scheduler_jobs WHERE id=?", job_id)
        await conn.commit()


async def get_all_scheduler_jobs() -> list[dict]:
    async with _db.acquire() as conn:
        return await conn.fetch("SELECT * FROM scheduler_jobs ORDER BY run_date")


async def get_scheduler_job(job_id: str) -> dict | None:
    async with _db.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM scheduler_jobs WHERE id=?", job_id)


async def delete_old_scheduler_jobs():
    # L-2 FIX: also remove jobs for bookings that are no longer active
    async with _db.acquire() as conn:
        # Remove past-due jobs
        await conn.execute(
            "DELETE FROM scheduler_jobs WHERE run_date < ?",
            get_now(config.TIMEZONE).isoformat(),
        )
        # Remove orphan jobs whose booking is completed or cancelled
        await conn.execute(
            "DELETE FROM scheduler_jobs WHERE booking_id IN ("
            "  SELECT sj.booking_id FROM scheduler_jobs sj"
            "  LEFT JOIN bookings b ON b.id = sj.booking_id"
            "  WHERE b.id IS NULL OR b.status != 'active'"
            ")"
        )
        await conn.commit()


# ======================================================================
# Scheduler distributed lock
# ======================================================================

def _scheduler_lock_expiry(ttl_seconds: int | None) -> tuple[str, str]:
    ttl = int(ttl_seconds or getattr(config, "SCHEDULER_LOCK_TTL_SECONDS", 120))
    if ttl <= 0:
        ttl = 120
    now = get_now(config.TIMEZONE)
    return now.isoformat(), (now + timedelta(seconds=ttl)).isoformat()


async def acquire_scheduler_lock(lock_name: str, owner: str, ttl_seconds: int | None = None) -> bool:
    """Acquire or refresh a DB-backed scheduler lock.

    The lock can be stolen only after expires_at passes. The same owner may
    refresh its own lock, which keeps release safe and idempotent.
    """
    if not lock_name or not owner:
        return False
    now_iso, expires_iso = _scheduler_lock_expiry(ttl_seconds)
    try:
        async with _db.acquire() as conn:
            if _db.is_postgres():
                row = await conn.fetchrow(
                    """
                    INSERT INTO scheduler_locks (lock_name, owner, acquired_at, expires_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (lock_name) DO UPDATE SET
                        owner=EXCLUDED.owner,
                        acquired_at=EXCLUDED.acquired_at,
                        expires_at=EXCLUDED.expires_at
                    WHERE scheduler_locks.expires_at <= EXCLUDED.acquired_at
                       OR scheduler_locks.owner = EXCLUDED.owner
                    RETURNING owner
                    """,
                    lock_name, owner, now_iso, expires_iso,
                )
                return bool(row and row.get("owner") == owner)

            async with conn.transaction():
                await conn.execute("DELETE FROM scheduler_locks WHERE expires_at <= ?", now_iso)
                existing = await conn.fetchrow(
                    "SELECT owner FROM scheduler_locks WHERE lock_name=?",
                    lock_name,
                )
                if existing and existing.get("owner") != owner:
                    return False
                if existing:
                    await conn.execute(
                        "UPDATE scheduler_locks SET owner=?, acquired_at=?, expires_at=? WHERE lock_name=?",
                        owner, now_iso, expires_iso, lock_name,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO scheduler_locks (lock_name, owner, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
                        lock_name, owner, now_iso, expires_iso,
                    )
                return True
    except Exception as e:
        if _is_unique_constraint_error(e):
            return False
        logger.warning(f"Failed to acquire scheduler lock {lock_name}: {e}")
        return False


async def release_scheduler_lock(lock_name: str, owner: str) -> bool:
    if not lock_name or not owner:
        return False
    try:
        async with _db.acquire() as conn:
            deleted = await conn.execute_count(
                "DELETE FROM scheduler_locks WHERE lock_name=? AND owner=?",
                lock_name, owner,
            )
            return deleted > 0
    except Exception as e:
        logger.warning(f"Failed to release scheduler lock {lock_name}: {e}")
        return False


async def get_scheduler_lock_status(lock_name: str = "scheduler") -> dict:
    now_iso = get_now(config.TIMEZONE).isoformat()
    try:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT lock_name, owner, acquired_at, expires_at FROM scheduler_locks WHERE lock_name=?",
                lock_name,
            )
        if not row:
            return {"lock_name": lock_name, "locked": False, "status": "free"}
        locked = row["expires_at"] > now_iso
        return {
            "lock_name": row["lock_name"],
            "locked": locked,
            "status": "held" if locked else "expired",
            "owner": row["owner"],
            "acquired_at": row["acquired_at"],
            "expires_at": row["expires_at"],
        }
    except Exception as e:
        logger.warning(f"Failed to read scheduler lock status {lock_name}: {e}")
        return {"lock_name": lock_name, "locked": False, "status": "error", "error": str(e)}


async def cleanup_expired_scheduler_locks() -> int:
    try:
        async with _db.acquire() as conn:
            return await conn.execute_count(
                "DELETE FROM scheduler_locks WHERE expires_at <= ?",
                get_now(config.TIMEZONE).isoformat(),
            )
    except Exception as e:
        logger.warning(f"Failed to cleanup expired scheduler locks: {e}")
        return 0


# ======================================================================
# Slot locks
# ======================================================================

async def create_slot_lock(
    date: str,
    time: str,
    master: str,
    ttl_minutes: int = 5,
    duration_minutes: int | None = None,
    owner_id: int | None = None,
    owner_token: str | None = None,
) -> bool:
    try:
        now = get_now(config.TIMEZONE)
        requested_slots = slot_times_for_range(time, duration_minutes or config.SLOT_STEP_MINUTES)
        master_key = normalize_master_key()
        owner_token = owner_token or ""
        async with _db.acquire() as conn:
            async with conn.transaction():
                now_iso = now.isoformat()
                expires_iso = (now + timedelta(minutes=ttl_minutes)).isoformat()
                if _db.is_postgres():
                    for lock_time in requested_slots:
                        row = await conn.fetchrow(
                            """
                            INSERT INTO slot_locks
                                (date, time, master, master_key, owner_id, owner_token, locked_at, expires_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT (date, time, master_key) DO UPDATE SET
                                master=EXCLUDED.master,
                                owner_id=EXCLUDED.owner_id,
                                owner_token=EXCLUDED.owner_token,
                                locked_at=EXCLUDED.locked_at,
                                expires_at=EXCLUDED.expires_at
                            WHERE slot_locks.expires_at <= EXCLUDED.locked_at
                               OR (
                                    slot_locks.owner_id IS NOT DISTINCT FROM EXCLUDED.owner_id
                                    AND COALESCE(slot_locks.owner_token, '') = COALESCE(EXCLUDED.owner_token, '')
                               )
                            RETURNING owner_id, owner_token
                            """,
                            date,
                            lock_time,
                            master,
                            master_key,
                            owner_id,
                            owner_token,
                            now_iso,
                            expires_iso,
                        )
                        if not row:
                            raise _SlotLockRejected("slot lock is held by another owner")
                    return True

                await conn.execute("DELETE FROM slot_locks WHERE expires_at <= ?", now_iso)
                for lock_time in requested_slots:
                    existing = await conn.fetchrow(
                        "SELECT owner_id, owner_token FROM slot_locks "
                        "WHERE date=? AND time=? AND master_key=? AND expires_at > ?",
                        date, lock_time, master_key, now_iso,
                    )
                    if existing and not (
                        owner_id is not None
                        and existing.get("owner_id") == owner_id
                        and (existing.get("owner_token") or "") == owner_token
                    ):
                        return False
                for lock_time in requested_slots:
                    await conn.execute(
                        "INSERT OR IGNORE INTO slot_locks "
                        "(date, time, master, master_key, owner_id, owner_token, locked_at, expires_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        date,
                        lock_time,
                        master,
                        master_key,
                        owner_id,
                        owner_token,
                        now_iso,
                        expires_iso,
                    )
                    await conn.execute(
                        "UPDATE slot_locks SET owner_id=?, owner_token=?, locked_at=?, expires_at=? "
                        "WHERE date=? AND time=? AND master_key=?",
                        owner_id,
                        owner_token,
                        now_iso,
                        expires_iso,
                        date,
                        lock_time,
                        master_key,
                    )
                return True
    except _SlotLockRejected:
        return False
    except Exception as e:
        if _is_unique_constraint_error(e):
            await release_slot_lock(
                date, time, master, duration_minutes=duration_minutes,
                owner_id=owner_id, owner_token=owner_token,
            )
            return False
        logger.warning(f"Failed to create slot_lock {date}/{time}/{master}: {e}")
        return False


async def release_slot_lock(
    date: str,
    time: str,
    master: str,
    duration_minutes: int | None = None,
    owner_id: int | None = None,
    owner_token: str | None = None,
):
    try:
        requested_slots = slot_times_for_range(time, duration_minutes or config.SLOT_STEP_MINUTES)
        master_key = normalize_master_key()
        owner_token = owner_token or ""
        async with _db.acquire() as conn:
            for lock_time in requested_slots:
                if owner_id is None and not owner_token:
                    await conn.execute(
                        "DELETE FROM slot_locks WHERE date=? AND time=? AND master_key=? AND owner_id IS NULL AND COALESCE(owner_token, '')=''",
                        date, lock_time, master_key,
                    )
                else:
                    if owner_token:
                        await conn.execute(
                            "DELETE FROM slot_locks WHERE date=? AND time=? AND master_key=? AND owner_id=? AND COALESCE(owner_token, '')=?",
                            date, lock_time, master_key, owner_id, owner_token,
                        )
                    else:
                        await conn.execute(
                            "DELETE FROM slot_locks WHERE date=? AND time=? AND master_key=? "
                            "AND ((owner_id=? AND COALESCE(owner_token, '')='') OR (owner_id IS NULL AND COALESCE(owner_token, '')=''))",
                            date, lock_time, master_key, owner_id,
                        )
            await conn.commit()
    except Exception as e:
        logger.warning(f"Failed to release slot_lock {date}/{time}/{master}: {e}")


async def cleanup_slot_locks_on_startup():
    try:
        async with _db.acquire() as conn:
            await conn.execute("DELETE FROM slot_locks")
            await conn.commit()
            logger.info("slot_locks cleared on startup")
    except Exception as e:
        logger.warning(f"Failed to cleanup slot_locks: {e}")


async def cleanup_expired_slot_locks() -> int:
    try:
        now = get_now(config.TIMEZONE).isoformat()
        async with _db.acquire() as conn:
            deleted = await conn.execute_count("DELETE FROM slot_locks WHERE expires_at < ?", now)
            if deleted:
                logger.info(f"Periodic cleanup: removed {deleted} expired slot_lock(s)")
            return deleted
    except Exception as e:
        logger.warning(f"Failed to cleanup expired slot_locks: {e}")
        return 0


# ======================================================================
# Master Telegram IDs (stored in settings)
# ======================================================================

async def get_master_telegram_id(master_name: str) -> int | None:
    value = await get_settings(f"master_tg_{master_name}")
    try:
        return int(value) if value else None
    except ValueError:
        return None


async def set_master_telegram_id(master_name: str, telegram_id: int | None):
    key = f"master_tg_{master_name}"
    if telegram_id:
        await save_settings(key, str(telegram_id))
    else:
        async with _db.acquire() as conn:
            await conn.execute("DELETE FROM settings WHERE key=?", key)
            await conn.commit()


async def get_all_master_telegram_ids() -> dict:
    async with _db.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM settings WHERE key LIKE 'master_tg_%'")
        result = {}
        for r in rows:
            master_name = r["key"][len("master_tg_"):]
            try:
                result[master_name] = int(r["value"])
            except ValueError:
                pass
        return result
