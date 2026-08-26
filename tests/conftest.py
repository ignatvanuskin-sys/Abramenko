# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com

import os
import sys
import pathlib
import pytest
from unittest.mock import AsyncMock, MagicMock

# Add project root to path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite database."""
    db_file = tmp_path / "test.db"
    import config
    import db as db_module
    import demo_repository
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(db_module, "_use_pg", False)
    monkeypatch.setattr(demo_repository, "_SCHEMA_READY", False)
    return str(db_file)


@pytest.fixture
async def db(isolated_db):
    """Initialized database ready for use."""
    import storage
    await storage.init_db()
    return isolated_db
