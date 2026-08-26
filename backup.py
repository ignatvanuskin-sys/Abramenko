# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import shutil
import os
import gzip
import logging
import asyncio
import sqlite3
import tempfile
import re
import subprocess
from datetime import datetime
import config
from monitoring import increment_counter, log_event

logger = logging.getLogger(__name__)

_BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")

_REQUIRED_TABLES = {
    "users", "bookings", "booking_slots", "waitlist", "loyalty", "referrals",
    "reviews", "settings", "services", "scheduler_jobs", "slot_locks",
    "scheduler_locks", "unavailable_periods", "portfolio_photos", "social_links",
    "admin_audit_log",
}

_POSTGRES_RESTORE_URL_ENV = "POSTGRES_RESTORE_TEST_DATABASE_URL"


def is_s3_backup_configured() -> bool:
    return all([
        config.S3_ENDPOINT_URL,
        config.S3_BUCKET,
        config.S3_ACCESS_KEY_ID,
        config.S3_SECRET_ACCESS_KEY,
    ])


def _s3_object_key(backup_file: str) -> str:
    filename = os.path.basename(backup_file)
    return f"{config.S3_BACKUP_PREFIX}/{filename}" if config.S3_BACKUP_PREFIX else filename


def upload_backup_to_s3(backup_file: str) -> str | None:
    """Upload a local backup to an S3-compatible endpoint when configured."""
    if not is_s3_backup_configured():
        logger.info("S3 backup upload disabled: S3 env vars are not fully configured")
        return None
    try:
        import boto3

        key = _s3_object_key(backup_file)
        client = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL,
            aws_access_key_id=config.S3_ACCESS_KEY_ID,
            aws_secret_access_key=config.S3_SECRET_ACCESS_KEY,
            region_name=config.S3_REGION,
        )
        client.upload_file(backup_file, config.S3_BUCKET, key)
        log_event(logger, "backup_s3_upload_success", bucket=config.S3_BUCKET, key=key)
        return key
    except Exception as e:
        logger.error(f"S3 backup upload failed: {e}")
        log_event(logger, "backup_s3_upload_failed", error=str(e))
        return None


async def _pg_dump(database_url: str, backup_file: str) -> bool:
    """Create a full PostgreSQL schema+data dump using pg_dump."""
    return await asyncio.to_thread(_pg_dump_sync, database_url, backup_file)


def _pg_dump_sync(database_url: str, backup_file: str) -> bool:
    cmd = [
        "pg_dump",
        "--format=plain",
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists",
        database_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except FileNotFoundError:
        logger.error("pg_dump failed: pg_dump executable not found")
        return False
    except Exception as e:
        logger.error(f"pg_dump failed: {e}")
        return False

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:2000]
        logger.error(f"pg_dump failed with exit code {result.returncode}: {stderr}")
        return False
    if not result.stdout:
        logger.error("pg_dump failed: empty dump")
        return False

    dump_bytes = re.sub(rb"(?m)^SET transaction_timeout = 0;\r?\n", b"", result.stdout)
    with gzip.open(backup_file, "wb", compresslevel=6) as f:
        f.write(dump_bytes)
    return _restore_sql_dump_to_temp_sqlite(backup_file)


def _backup_sqlite_database(source_path: str, backup_file: str) -> None:
    if not os.path.exists(source_path):
        raise FileNotFoundError(source_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        snapshot_path = os.path.join(tmp_dir, "snapshot.db")
        src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        dst = sqlite3.connect(snapshot_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(snapshot_path, "rb") as f_in, gzip.open(backup_file, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)


def backup_database():
    """CRIT-01 FIX: Backup both SQLite (gzip) and PostgreSQL (SQL dump, gzip).
    H-3 FIX: use explicit event loop (safe under uvloop or any custom policy).
    Called via asyncio.to_thread -- the thread has no running loop."""
    try:
        os.makedirs(_BACKUP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        database_url = os.getenv("DATABASE_URL", "").strip()

        if database_url:
            backup_file = os.path.join(_BACKUP_DIR, f"nailshop_{timestamp}.sql.gz")
            _loop = asyncio.new_event_loop()
            try:
                success = _loop.run_until_complete(_pg_dump(database_url, backup_file))
            finally:
                _loop.close()
            if success:
                logger.info(f"PostgreSQL backup created: {backup_file}")
                _after_local_backup_created(backup_file)
                return backup_file
            else:
                logger.error("PostgreSQL backup FAILED -- check logs above")
                increment_counter("backup_failed")
                return None
        else:
            backup_file = os.path.join(_BACKUP_DIR, f"nailshop_{timestamp}.db.gz")
            _backup_sqlite_database(config.DB_PATH, backup_file)
            logger.info(f"SQLite backup created: {backup_file}")
            _after_local_backup_created(backup_file)
            return backup_file
    except Exception as e:
        logger.error(f"Failed to backup database: {e}")
        increment_counter("backup_failed")
        return None


def _after_local_backup_created(backup_file: str) -> None:
    s3_key = upload_backup_to_s3(backup_file)
    if is_s3_backup_configured() and not s3_key:
        increment_counter("backup_failed")
        return
    increment_counter("backup_success")
    log_event(logger, "backup_success", local_path=backup_file, s3_key=s3_key or "")


def cleanup_old_backups(max_backups: int = 30):
    """Remove old backup files, keeping max_backups most recent."""
    try:
        if not os.path.exists(_BACKUP_DIR):
            return
        all_files = sorted(os.listdir(_BACKUP_DIR))
        backup_files = [f for f in all_files if f.endswith(".db.gz") or f.endswith(".sql.gz")]
        if len(backup_files) > max_backups:
            for f in backup_files[:-max_backups]:
                os.remove(os.path.join(_BACKUP_DIR, f))
                logger.info(f"Removed old backup: {f}")
    except Exception as e:
        logger.error(f"Failed to cleanup backups: {e}")


def get_latest_backup() -> str | None:
    if not os.path.exists(_BACKUP_DIR):
        return None
    candidates = [
        os.path.join(_BACKUP_DIR, name)
        for name in os.listdir(_BACKUP_DIR)
        if name.endswith(".db.gz") or name.endswith(".sql.gz")
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _restore_sql_dump_to_temp_sqlite(backup_file: str) -> bool:
    with gzip.open(backup_file, "rt", encoding="utf-8") as f:
        dump_text = f.read()
    if not dump_text.strip():
        logger.error("Restore-check failed: SQL dump is empty")
        return False
    lowered = dump_text.lower()
    if "create table" not in lowered:
        logger.error("Restore-check failed: SQL dump has no schema")
        return False

    found_tables = set()
    for match in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:(?:public)\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
        dump_text,
        re.IGNORECASE,
    ):
        found_tables.add(match.group(1))

    missing = sorted(_REQUIRED_TABLES - found_tables)
    if missing:
        logger.error("Restore-check failed: SQL dump missing tables: %s", ", ".join(missing))
        return False

    if "booking_slots" not in lowered or "unavailable_periods" not in lowered:
        logger.error("Restore-check failed: critical booking integrity tables are absent")
        return False
    if not re.search(r"SELECT\s+pg_catalog\.setval|setval\(", dump_text, re.IGNORECASE):
        logger.warning("Restore-check warning: no sequence setval statements found")
    return True


def _psql(database_url: str, *, sql: str | None = None, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    cmd = ["psql", database_url, "-v", "ON_ERROR_STOP=1"]
    if sql is not None:
        cmd.extend(["-c", sql])
    return subprocess.run(cmd, input=stdin, capture_output=True, check=False)


def _psql_error(result: subprocess.CompletedProcess) -> str:
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    return (stderr or stdout)[:2000]


def _restore_sql_dump_to_postgres(backup_file: str, database_url: str) -> bool:
    if not database_url:
        logger.warning(
            "PostgreSQL real restore skipped: set %s to a disposable restore-test database URL",
            _POSTGRES_RESTORE_URL_ENV,
        )
        return _restore_sql_dump_to_temp_sqlite(backup_file)

    try:
        with gzip.open(backup_file, "rb") as f:
            dump_bytes = f.read()
    except Exception as e:
        logger.error(f"PostgreSQL restore-check failed: cannot read dump: {e}")
        return False

    try:
        result = _psql(database_url, stdin=dump_bytes)
    except FileNotFoundError:
        logger.error("PostgreSQL restore-check failed: psql executable not found")
        return False
    except Exception as e:
        logger.error(f"PostgreSQL restore-check failed while running psql: {e}")
        return False
    if result.returncode != 0:
        logger.error("PostgreSQL restore-check failed while applying dump: %s", _psql_error(result))
        return False

    required = ", ".join(f"'{table}'" for table in sorted(_REQUIRED_TABLES))
    tables_sql = (
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname='public' AND tablename IN (" + required + ") "
        "ORDER BY tablename"
    )
    result = _psql(database_url, sql=tables_sql)
    if result.returncode != 0:
        logger.error("PostgreSQL restore-check failed while checking tables: %s", _psql_error(result))
        return False
    restored_tables = set((result.stdout or b"").decode("utf-8", errors="replace").split())
    missing = sorted(_REQUIRED_TABLES - restored_tables)
    if missing:
        logger.error("PostgreSQL restore-check failed: restored DB missing tables: %s", ", ".join(missing))
        return False

    counts_sql = "SELECT (SELECT COUNT(*) FROM booking_slots), (SELECT COUNT(*) FROM unavailable_periods)"
    result = _psql(database_url, sql=counts_sql)
    if result.returncode != 0:
        logger.error("PostgreSQL restore-check failed while checking critical table counts: %s", _psql_error(result))
        return False
    logger.info(
        "PostgreSQL restore-check critical counts: %s",
        (result.stdout or b"").decode("utf-8", errors="replace").strip(),
    )

    unique_sql = """
    BEGIN;
    INSERT INTO booking_slots (booking_id, date, master_key, slot_time, created_at)
    VALUES ('restore_check_a', '9999-12-31', 'restore_check', '23:30', '9999-12-31T23:30:00+00:00');
    INSERT INTO booking_slots (booking_id, date, master_key, slot_time, created_at)
    VALUES ('restore_check_b', '9999-12-31', 'restore_check', '23:30', '9999-12-31T23:30:00+00:00');
    ROLLBACK;
    """
    result = _psql(database_url, sql=unique_sql)
    error_text = _psql_error(result).lower()
    if result.returncode == 0:
        logger.error("PostgreSQL restore-check failed: booking_slots unique constraint did not reject duplicates")
        return False
    if "duplicate" not in error_text and "unique" not in error_text:
        logger.error("PostgreSQL restore-check failed: constraint probe failed unexpectedly: %s", _psql_error(result))
        return False

    logger.info("PostgreSQL real restore-check succeeded")
    return True


def restore_check(backup_file: str | None = None, postgres_restore_url: str | None = None) -> bool:
    """Validate the latest/local backup by restoring it into a temporary DB when possible."""
    backup_file = backup_file or get_latest_backup()
    if not backup_file or not os.path.exists(backup_file):
        logger.error("Restore-check failed: backup file not found")
        log_event(logger, "restore_check_failed", reason="missing_backup")
        return False

    try:
        if backup_file.endswith(".db.gz"):
            with tempfile.TemporaryDirectory() as tmp_dir:
                restored_db = os.path.join(tmp_dir, "restore_check.db")
                with gzip.open(backup_file, "rb") as f_in, open(restored_db, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                conn = sqlite3.connect(restored_db)
                try:
                    result = conn.execute("PRAGMA integrity_check").fetchone()
                    ok = bool(result and result[0] == "ok")
                finally:
                    conn.close()
                if ok:
                    log_event(logger, "restore_check_success", backup_file=backup_file, backend="sqlite")
                    return True
                logger.error("Restore-check failed: SQLite integrity_check did not return ok")
                log_event(logger, "restore_check_failed", backup_file=backup_file, backend="sqlite")
                return False

        if backup_file.endswith(".sql.gz"):
            restore_url = postgres_restore_url or os.getenv(_POSTGRES_RESTORE_URL_ENV, "").strip()
            ok = _restore_sql_dump_to_postgres(backup_file, restore_url)
            if ok:
                backend = "postgres_real_restore" if restore_url else "postgres_dump_structural_check"
                log_event(logger, "restore_check_success", backup_file=backup_file, backend=backend)
                return True
            backend = "postgres_real_restore" if restore_url else "postgres_dump_structural_check"
            log_event(logger, "restore_check_failed", backup_file=backup_file, backend=backend)
            return False

        logger.error(f"Restore-check failed: unsupported backup extension: {backup_file}")
        log_event(logger, "restore_check_failed", backup_file=backup_file, reason="unsupported_extension")
        return False
    except Exception as e:
        logger.error(f"Restore-check failed: {e}")
        log_event(logger, "restore_check_failed", backup_file=backup_file, error=str(e))
        return False


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Create or validate nailshop backups")
    parser.add_argument("--restore-check", nargs="?", const="", help="Validate a backup file or latest backup")
    parser.add_argument(
        "--postgres-restore-url",
        default="",
        help=f"Disposable PostgreSQL URL for real .sql.gz restore drill (or set {_POSTGRES_RESTORE_URL_ENV})",
    )
    args = parser.parse_args(argv)

    if args.restore_check is not None:
        ok = restore_check(args.restore_check or None, postgres_restore_url=args.postgres_restore_url.strip() or None)
        print("restore-check: ok" if ok else "restore-check: failed")
        return 0 if ok else 1

    backup_file = backup_database()
    if not backup_file:
        return 1
    return 0 if restore_check(backup_file) else 1


if __name__ == "__main__":
    raise SystemExit(main())
