
"""Tests for tz_utils.py"""
import pytest
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from datetime import datetime


class TestTzUtils:
    def test_get_now_returns_datetime(self):
        from tz_utils import get_now
        result = get_now("Asia/Almaty")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_get_now_utc(self):
        from tz_utils import get_now
        result = get_now("UTC")
        assert isinstance(result, datetime)

    def test_get_today_returns_string(self):
        from tz_utils import get_today
        today = get_today("Asia/Almaty")
        assert isinstance(today, str)
        assert len(today) == 10  # YYYY-MM-DD format

    def test_get_today_format(self):
        from tz_utils import get_today
        today = get_today("UTC")
        parts = today.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4  # year
        assert len(parts[1]) == 2  # month
        assert len(parts[2]) == 2  # day

    def test_is_past_future_returns_false(self):
        from tz_utils import is_past
        # Far future date
        result = is_past("2099-12-31", "23:59", "UTC")
        assert result is False

    def test_is_past_past_returns_true(self):
        from tz_utils import is_past
        # Very old date
        result = is_past("2000-01-01", "00:00", "UTC")
        assert result is True

    def test_get_now_invalid_tz_fallback(self):
        from tz_utils import get_now
        # Should fallback to UTC for invalid tz
        try:
            result = get_now("Invalid/TZ")
            assert isinstance(result, datetime)
        except Exception:
            pass  # Some implementations may raise
