# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import gzip
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _set_s3_config(monkeypatch, configured: bool):
    import config

    monkeypatch.setattr(config, "S3_ENDPOINT_URL", "https://s3.example.test" if configured else "")
    monkeypatch.setattr(config, "S3_BUCKET", "nailshop-backups" if configured else "")
    monkeypatch.setattr(config, "S3_ACCESS_KEY_ID", "key" if configured else "")
    monkeypatch.setattr(config, "S3_SECRET_ACCESS_KEY", "secret" if configured else "")
    monkeypatch.setattr(config, "S3_REGION", "eu-test-1")
    monkeypatch.setattr(config, "S3_BACKUP_PREFIX", "prod/backups")


def test_s3_disabled_upload_returns_none(tmp_path, monkeypatch):
    import backup

    _set_s3_config(monkeypatch, configured=False)
    backup_file = tmp_path / "nailshop.db.gz"
    backup_file.write_bytes(b"data")

    assert backup.is_s3_backup_configured() is False
    assert backup.upload_backup_to_s3(str(backup_file)) is None


def test_mocked_s3_upload(tmp_path, monkeypatch):
    import backup

    _set_s3_config(monkeypatch, configured=True)
    backup_file = tmp_path / "nailshop_20260101_000000.db.gz"
    backup_file.write_bytes(b"backup")
    client = MagicMock()
    fake_boto3 = SimpleNamespace(client=MagicMock(return_value=client))
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    key = backup.upload_backup_to_s3(str(backup_file))

    assert key == "prod/backups/nailshop_20260101_000000.db.gz"
    fake_boto3.client.assert_called_once()
    client.upload_file.assert_called_once_with(str(backup_file), "nailshop-backups", key)


def test_local_backup_still_works_when_s3_disabled(tmp_path, monkeypatch):
    import backup

    _set_s3_config(monkeypatch, configured=False)
    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO sample (value) VALUES ('ok')")
    conn.commit()
    conn.close()

    with (
        patch("backup._BACKUP_DIR", str(tmp_path / "backups")),
        patch("backup.config.DB_PATH", str(db_path)),
        patch("backup.os.getenv", return_value=""),
    ):
        result = backup.backup_database()

    assert result is not None
    assert result.endswith(".db.gz")
    assert backup.restore_check(result) is True


def test_restore_check_valid_sqlite_backup(tmp_path):
    import backup

    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    backup_file = tmp_path / "valid.db.gz"
    with open(db_path, "rb") as f_in, gzip.open(backup_file, "wb") as f_out:
        f_out.write(f_in.read())

    assert backup.restore_check(str(backup_file)) is True


def test_restore_check_invalid_sqlite_backup(tmp_path):
    import backup

    backup_file = tmp_path / "broken.db.gz"
    with gzip.open(backup_file, "wb") as f:
        f.write(b"not a sqlite database")

    assert backup.restore_check(str(backup_file)) is False


def test_restore_check_sql_dump_header(tmp_path):
    import backup
    from tests.test_production_hardening import _pg_dump_with_required_tables

    backup_file = tmp_path / "dump.sql.gz"
    with gzip.open(backup_file, "wt", encoding="utf-8") as f:
        f.write(_pg_dump_with_required_tables())

    assert backup.restore_check(str(backup_file)) is True


def test_restore_check_sql_dump_rejects_bad_sql(tmp_path):
    import backup

    backup_file = tmp_path / "bad_dump.sql.gz"
    with gzip.open(backup_file, "wt", encoding="utf-8") as f:
        f.write("CREATE TABLE public.users (id integer);\n")

    assert backup.restore_check(str(backup_file)) is False


def test_restore_check_script_main_success(tmp_path):
    import restore_check as restore_check_script

    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    backup_file = tmp_path / "source.db.gz"
    with open(db_path, "rb") as f_in, gzip.open(backup_file, "wb") as f_out:
        f_out.write(f_in.read())

    assert restore_check_script.main([str(backup_file)]) == 0
