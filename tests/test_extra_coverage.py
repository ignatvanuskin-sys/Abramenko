# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import sys, pathlib, subprocess
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


class TestTzUtilsExtra:

    def test_format_datetime_with_tz(self):
        import tz_utils
        from datetime import timezone
        dt = datetime(2099, 6, 15, 10, 30, tzinfo=timezone.utc)
        result = tz_utils.format_datetime(dt)
        assert "2099" in result

    def test_format_datetime_custom_tz(self):
        import tz_utils
        from datetime import timezone
        dt = datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)
        result = tz_utils.format_datetime(dt, "Europe/Moscow")
        assert "2026" in result

    def test_get_tomorrow(self):
        import tz_utils
        result = tz_utils.get_tomorrow()
        assert len(result) == 10
        assert result[4] == "-"

    def test_get_tomorrow_custom_tz(self):
        import tz_utils
        result = tz_utils.get_tomorrow("Europe/Moscow")
        assert len(result) == 10

    def test_is_past_past_date(self):
        import tz_utils
        result = tz_utils.is_past("2020-01-01", "10:00")
        assert result is True

    def test_is_past_future_date(self):
        import tz_utils
        result = tz_utils.is_past("2099-12-31", "23:59")
        assert result is False


class TestUtilsExtra:

    async def test_edit_with_retry_exhausted(self):
        """Lines 54-55: exhausted retries logs error"""
        from utils import edit_with_retry
        msg = MagicMock()
        msg.edit_text = AsyncMock(side_effect=Exception("cannot edit"))
        result = await edit_with_retry(msg, "text", max_retries=2)
        assert result is False

    async def test_notify_admins_exception_handled(self):
        """Lines 66-67: exception per admin is caught"""
        from utils import notify_admins
        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=Exception("blocked"))
        with patch("config.ADMIN_IDS", [100, 200]):
            await notify_admins(bot, "test text")

    async def test_notify_admins_multiple(self):
        """All configured admins receive the notification."""
        from utils import notify_admins
        bot = AsyncMock()
        bot.send_message = AsyncMock()
        with patch("config.ADMIN_IDS", [100, 200]):
            await notify_admins(bot, "test text")
        assert bot.send_message.call_count == 2
        bot.send_message.assert_any_call(100, "test text", parse_mode="HTML")
        bot.send_message.assert_any_call(200, "test text", parse_mode="HTML")


class TestEmojiConfigExtra:

    def test_check_emoji_config_stats(self):
        """Lines 192-196: check_emoji_config returns stats dict"""
        import emoji_config
        result = emoji_config.check_emoji_config()
        assert "total" in result
        assert "configured" in result
        assert "missing" in result
        assert "percent" in result
        assert isinstance(result["percent"], float)

    def test_check_emoji_config_empty_dict(self):
        """Lines 192-196: empty CUSTOM_EMOJIS -> percent 0"""
        import emoji_config
        with patch.object(emoji_config, "CUSTOM_EMOJIS", {}):
            result = emoji_config.check_emoji_config()
        assert result["percent"] == 0
        assert result["total"] == 0

    def test_class_P_has_attributes(self):
        """Lines 206-224: class P exists with emoji attrs"""
        import emoji_config
        p = emoji_config.P
        assert hasattr(p, "CHECK") or hasattr(p, "CROSS") or hasattr(p, "EMPTY")


class TestBackupPgDump:

    async def test_pg_dump_with_rows(self):
        """_pg_dump writes a full pg_dump plain SQL gzip when pg_dump succeeds."""
        import backup
        from tests.test_production_hardening import _pg_dump_with_required_tables

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".sql.gz", delete=False) as f:
            tmp = f.name

        try:
            completed = subprocess.CompletedProcess(
                args=["pg_dump"], returncode=0, stdout=_pg_dump_with_required_tables().encode("utf-8"), stderr=b""
            )
            with patch("backup.subprocess.run", return_value=completed):
                result = await backup._pg_dump("postgresql://localhost/test", tmp)
            assert result is True
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    async def test_pg_dump_table_fetch_exception(self):
        """_pg_dump returns False when pg_dump output is incomplete."""
        import backup

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".sql.gz", delete=False) as f:
            tmp = f.name
        try:
            completed = subprocess.CompletedProcess(
                args=["pg_dump"], returncode=0, stdout=b"CREATE TABLE public.users (id integer);", stderr=b""
            )
            with patch("backup.subprocess.run", return_value=completed):
                result = await backup._pg_dump("postgresql://localhost/test", tmp)
            assert result is False
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
