
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
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    return str(db_file)


@pytest.fixture
async def db(isolated_db):
    """Initialized database ready for use."""
    import storage
    await storage.init_db()
    return isolated_db
