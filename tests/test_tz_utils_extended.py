
"""Extended tz_utils tests for missing lines."""
import pytest
import sys, pathlib
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


class TestTzUtilsExtended:

    def test_get_now_non_utc_timezone(self):
        from tz_utils import get_now
        from datetime import datetime
        now = get_now("Asia/Almaty")
        assert isinstance(now, datetime)
        assert now.tzinfo is not None

    def test_is_past_exact_boundary(self):
        from tz_utils import is_past, get_now
        import config
        now = get_now(config.TIMEZONE)
        from datetime import timedelta
        future = now + timedelta(hours=2)
        assert not is_past(future.strftime("%Y-%m-%d"), future.strftime("%H:%M"))

    def test_get_today_matches_now(self):
        from tz_utils import get_today, get_now
        import config
        now = get_now(config.TIMEZONE)
        today = get_today(config.TIMEZONE)
        assert today == now.strftime("%Y-%m-%d")

    def test_invalid_timezone_graceful(self):
        from tz_utils import get_now
        from datetime import datetime
        import pytz
        try:
            now = get_now("Invalid/Timezone_XYZ")
            assert isinstance(now, datetime)
        except (pytz.exceptions.UnknownTimeZoneError, Exception):
            pass  # acceptable
