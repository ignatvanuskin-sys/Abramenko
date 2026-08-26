# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com

"""Tests for updated backup.py: gzip output and PG mock."""
import gzip, os, pytest, sys, pathlib, sqlite3, subprocess
from unittest.mock import patch, AsyncMock, MagicMock
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


class TestSqliteBackupGzip:

    def test_creates_db_gz_file(self, tmp_path):
        import backup
        db = tmp_path / "test.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with patch("backup._BACKUP_DIR", str(tmp_path / "backups")), \
             patch("backup.config.DB_PATH", str(db)), \
             patch("backup.os.getenv", return_value=""):
            result = backup.backup_database()
        assert result is not None
        assert result.endswith(".db.gz")
        assert os.path.exists(result)

    def test_backup_content_is_valid_gzip(self, tmp_path):
        import backup
        db = tmp_path / "test.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO sample (value) VALUES ('ok')")
        conn.commit()
        conn.close()
        with patch("backup._BACKUP_DIR", str(tmp_path / "backups")), \
             patch("backup.config.DB_PATH", str(db)), \
             patch("backup.os.getenv", return_value=""):
            result = backup.backup_database()
        assert result is not None
        with gzip.open(result, "rb") as f:
            content = f.read()
        restored = tmp_path / "restored.db"
        restored.write_bytes(content)
        restored_conn = sqlite3.connect(restored)
        try:
            assert restored_conn.execute("SELECT value FROM sample").fetchone()[0] == "ok"
        finally:
            restored_conn.close()

    def test_returns_none_when_db_missing(self, tmp_path):
        import backup
        with patch("backup._BACKUP_DIR", str(tmp_path / "backups")), \
             patch("backup.config.DB_PATH", str(tmp_path / "nonexistent.db")), \
             patch("backup.os.getenv", return_value=""):
            result = backup.backup_database()
        assert result is None

    def test_backup_dir_created_if_missing(self, tmp_path):
        import backup
        db = tmp_path / "test.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        backup_dir = tmp_path / "new_backups"
        assert not backup_dir.exists()
        with patch("backup._BACKUP_DIR", str(backup_dir)), \
             patch("backup.config.DB_PATH", str(db)), \
             patch("backup.os.getenv", return_value=""):
            backup.backup_database()
        assert backup_dir.exists()


class TestPostgresBackupMock:

    def _mock_loop(self, return_value):
        from unittest.mock import MagicMock
        loop = MagicMock()
        def _run_until_complete(coro):
            if hasattr(coro, "close"):
                coro.close()
            return return_value
        loop.run_until_complete = MagicMock(side_effect=_run_until_complete)
        loop.close = MagicMock()
        return loop

    def test_calls_new_event_loop_when_database_url_set(self, tmp_path):
        # H-3 FIX: backup.py uses asyncio.new_event_loop() instead of asyncio.run()
        import backup
        loop = self._mock_loop(True)
        with patch("backup._BACKUP_DIR", str(tmp_path / "backups")), \
             patch("backup.os.getenv", return_value="postgresql://localhost/test"), \
             patch("backup.asyncio.new_event_loop", return_value=loop):
            result = backup.backup_database()
        assert loop.run_until_complete.called
        assert result is not None
        assert result.endswith(".sql.gz")

    def test_returns_none_when_pg_dump_fails(self, tmp_path):
        import backup
        loop = self._mock_loop(False)
        with patch("backup._BACKUP_DIR", str(tmp_path / "backups")), \
             patch("backup.os.getenv", return_value="postgresql://localhost/test"), \
             patch("backup.asyncio.new_event_loop", return_value=loop):
            result = backup.backup_database()
        assert result is None

    async def test_pg_dump_returns_false_on_connection_error(self, tmp_path):
        import backup
        with patch("backup.subprocess.run", side_effect=FileNotFoundError("pg_dump")):
            result = await backup._pg_dump("postgresql://localhost/test", str(tmp_path / "dump.sql.gz"))
        assert result is False

    async def test_pg_dump_creates_gzip_file(self, tmp_path):
        import backup
        from tests.test_production_hardening import _pg_dump_with_required_tables

        completed = subprocess.CompletedProcess(
            args=["pg_dump"], returncode=0, stdout=_pg_dump_with_required_tables().encode("utf-8"), stderr=b""
        )
        out_file = str(tmp_path / "dump.sql.gz")
        with patch("backup.subprocess.run", return_value=completed):
            result = await backup._pg_dump("postgresql://localhost/test", out_file)
        assert result is True
        assert os.path.exists(out_file)
        with gzip.open(out_file, "rt", encoding="utf-8") as f:
            text = f.read()
        assert "CREATE TABLE public.booking_slots" in text

    async def test_pg_dump_strips_pg17_transaction_timeout_for_pg16_restore(self, tmp_path):
        import backup
        from tests.test_production_hardening import _pg_dump_with_required_tables

        dump = "SET transaction_timeout = 0;\n" + _pg_dump_with_required_tables()
        completed = subprocess.CompletedProcess(
            args=["pg_dump"], returncode=0, stdout=dump.encode("utf-8"), stderr=b""
        )
        out_file = str(tmp_path / "dump.sql.gz")
        with patch("backup.subprocess.run", return_value=completed):
            result = await backup._pg_dump("postgresql://localhost/test", out_file)
        assert result is True
        with gzip.open(out_file, "rt", encoding="utf-8") as f:
            text = f.read()
        assert "SET transaction_timeout" not in text
        assert "CREATE TABLE public.booking_slots" in text


class TestCleanupUpdated:

    def test_removes_excess_db_gz(self, tmp_path):
        import backup
        bd = tmp_path / "backups"
        bd.mkdir()
        for i in range(35):
            (bd / f"nailshop_{i:04d}0000_000000.db.gz").write_bytes(b"x")
        with patch("backup._BACKUP_DIR", str(bd)):
            backup.cleanup_old_backups(max_backups=30)
        assert len(list(bd.glob("*.db.gz"))) == 30

    def test_removes_excess_sql_gz(self, tmp_path):
        import backup
        bd = tmp_path / "backups"
        bd.mkdir()
        for i in range(40):
            (bd / f"nailshop_{i:04d}0000_000000.sql.gz").write_bytes(b"x")
        with patch("backup._BACKUP_DIR", str(bd)):
            backup.cleanup_old_backups(max_backups=30)
        assert len(list(bd.glob("*.sql.gz"))) == 30

    def test_mixed_extensions_counted_together(self, tmp_path):
        import backup
        bd = tmp_path / "backups"
        bd.mkdir()
        for i in range(20):
            (bd / f"nailshop_{i:04d}0000_000000.db.gz").write_bytes(b"x")
        for i in range(20):
            (bd / f"nailshop_{(i+20):04d}0000_000000.sql.gz").write_bytes(b"x")
        with patch("backup._BACKUP_DIR", str(bd)):
            backup.cleanup_old_backups(max_backups=30)
        total = len(list(bd.glob("*.db.gz"))) + len(list(bd.glob("*.sql.gz")))
        assert total == 30

    def test_other_files_untouched(self, tmp_path):
        import backup
        bd = tmp_path / "backups"
        bd.mkdir()
        (bd / "notes.txt").write_bytes(b"keep me")
        for i in range(5):
            (bd / f"nailshop_{i:04d}0000_000000.db.gz").write_bytes(b"x")
        with patch("backup._BACKUP_DIR", str(bd)):
            backup.cleanup_old_backups(max_backups=10)
        assert (bd / "notes.txt").exists()

    def test_no_dir_does_not_raise(self, tmp_path):
        import backup
        with patch("backup._BACKUP_DIR", str(tmp_path / "missing")):
            backup.cleanup_old_backups()

    def test_below_limit_nothing_removed(self, tmp_path):
        import backup
        bd = tmp_path / "backups"
        bd.mkdir()
        for i in range(5):
            (bd / f"nailshop_{i:04d}0000_000000.db.gz").write_bytes(b"x")
        with patch("backup._BACKUP_DIR", str(bd)):
            backup.cleanup_old_backups(max_backups=30)
        assert len(list(bd.glob("*.db.gz"))) == 5
