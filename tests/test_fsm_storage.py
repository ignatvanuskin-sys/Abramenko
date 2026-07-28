
"""Tests for fsm_storage.py - FileStorage"""
import pytest
import sys, pathlib, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from unittest.mock import MagicMock


@pytest.fixture
def fs(tmp_path):
    from fsm_storage import FileStorage
    return FileStorage(str(tmp_path / "fsm.json"))


class TestFileStorage:
    async def test_set_and_get_state(self, fs):
        key = MagicMock()
        key.chat_id = 111
        key.user_id = 111
        key.bot_id = 1
        key.destiny = "default"
        await fs.set_state(key=key, state="BookingStates:choose_master")
        state = await fs.get_state(key=key)
        assert state == "BookingStates:choose_master"

    async def test_get_nonexistent_state_returns_none(self, fs):
        key = MagicMock()
        key.chat_id = 999
        key.user_id = 999
        key.bot_id = 1
        key.destiny = "default"
        state = await fs.get_state(key=key)
        assert state is None

    async def test_set_and_get_data(self, fs):
        key = MagicMock()
        key.chat_id = 111
        key.user_id = 111
        key.bot_id = 1
        key.destiny = "default"
        await fs.set_data(key=key, data={"master": "Alibek", "service": "Haircut"})
        data = await fs.get_data(key=key)
        assert data["master"] == "Alibek"
        assert data["service"] == "Haircut"

    async def test_get_nonexistent_data_returns_empty(self, fs):
        key = MagicMock()
        key.chat_id = 999
        key.user_id = 999
        key.bot_id = 1
        key.destiny = "default"
        data = await fs.get_data(key=key)
        assert data == {}

    async def test_update_data(self, fs):
        key = MagicMock()
        key.chat_id = 111
        key.user_id = 111
        key.bot_id = 1
        key.destiny = "default"
        await fs.set_data(key=key, data={"master": "Alibek"})
        await fs.update_data(key=key, data={"service": "Haircut"})
        data = await fs.get_data(key=key)
        assert data["master"] == "Alibek"
        assert data["service"] == "Haircut"

    async def test_close(self, fs):
        await fs.close()
        await fs.wait_closed()

    async def test_multiple_users_isolated(self, fs):
        key1, key2 = MagicMock(), MagicMock()
        for key, uid in [(key1, 111), (key2, 222)]:
            key.chat_id = uid
            key.user_id = uid
            key.bot_id = 1
            key.destiny = "default"
        await fs.set_state(key=key1, state="state1")
        await fs.set_state(key=key2, state="state2")
        s1 = await fs.get_state(key=key1)
        s2 = await fs.get_state(key=key2)
        assert s1 == "state1"
        assert s2 == "state2"
        assert s1 != s2

    async def test_set_none_state(self, fs):
        key = MagicMock()
        key.chat_id = 111
        key.user_id = 111
        key.bot_id = 1
        key.destiny = "default"
        await fs.set_state(key=key, state="some_state")
        await fs.set_state(key=key, state=None)
        state = await fs.get_state(key=key)
        assert state is None

    async def test_clear_all_states(self, fs):
        key = MagicMock()
        key.chat_id = 111
        key.user_id = 111
        key.bot_id = 1
        key.destiny = "default"
        await fs.set_state(key=key, state="test_state")
        await fs.clear_all_states()
        state = await fs.get_state(key=key)
        assert state is None

    async def test_persistence_across_instances(self, tmp_path):
        from fsm_storage import FileStorage
        path = str(tmp_path / "fsm.json")
        fs1 = FileStorage(path)
        key = MagicMock()
        key.chat_id = 111
        key.user_id = 111
        key.bot_id = 1
        key.destiny = "default"
        await fs1.set_state(key=key, state="persisted_state")
        await fs1.close()
        # Create new instance with same file
        fs2 = FileStorage(path)
        state = await fs2.get_state(key=key)
        assert state == "persisted_state"
