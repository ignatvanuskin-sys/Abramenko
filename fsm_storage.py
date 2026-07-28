import json
import os
import asyncio
import logging
from typing import Optional

from aiogram.fsm.storage.base import BaseStorage

logger = logging.getLogger(__name__)

import config as _cfg
import pathlib as _pl
# RAILWAY FIX: абсолютный путь рядом с БД, не зависит от CWD
FSM_FILE = str(_pl.Path(_cfg.DB_PATH).parent / "fsm_state.json")


class FileStorage(BaseStorage):
    def __init__(self, filepath: str = FSM_FILE):
        super().__init__()
        self.filepath = filepath
        self._data = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self):
        try:
            _p=os.path.dirname(self.filepath)
            if _p: os.makedirs(_p,exist_ok=True)
            if os.path.exists(self.filepath):
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load FSM state: {e}")
            self._data = {}

    async def _save(self):
        """MED-05 FIX: atomic write via temp file + os.replace to prevent corruption."""
        async with self._lock:
            tmp_path = None
            try:
                import tempfile
                dirpath = os.path.dirname(os.path.abspath(self.filepath))
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=dirpath,
                    delete=False, suffix=".tmp"
                ) as tmp:
                    json.dump(self._data, tmp, ensure_ascii=False, indent=2)
                    tmp_path = tmp.name
                os.replace(tmp_path, self.filepath)
            except Exception as e:
                logger.error(f"Failed to save FSM state: {e}")
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

    def _key_to_str(self, key) -> str:
        if hasattr(key, "bot_id"):
            return f"{key.bot_id}:{key.chat_id}:{key.user_id}"
        return str(key)

    async def get_state(self, key) -> Optional[str]:
        k = self._key_to_str(key)
        return self._data.get(k, {}).get("state")

    async def set_state(self, key, state: Optional[str]) -> None:
        k = self._key_to_str(key)
        if state is None:
            if k in self._data:
                self._data[k].pop("state", None)
        else:
            if k not in self._data:
                self._data[k] = {}
            self._data[k]["state"] = state.state if hasattr(state, "state") else str(state)
        await self._save()

    async def get_data(self, key) -> dict:
        k = self._key_to_str(key)
        return self._data.get(k, {}).get("data", {})

    async def set_data(self, key, data: dict) -> None:
        k = self._key_to_str(key)
        if k not in self._data:
            self._data[k] = {}
        self._data[k]["data"] = data
        await self._save()

    async def update_data(self, key, data: dict) -> None:
        k = self._key_to_str(key)
        if k not in self._data:
            self._data[k] = {}
        if "data" not in self._data[k]:
            self._data[k]["data"] = {}
        self._data[k]["data"].update(data)
        await self._save()


    async def clear_all_states(self) -> None:
        """Clear all FSM states on restart to prevent users being stuck."""
        async with self._lock:
            self._data = {}
            try:
                import json, shutil, os
                # Backup existing states before clearing
                if os.path.exists(self.filepath) and os.path.getsize(self.filepath) > 2:
                    backup = self.filepath + ".bak"
                    shutil.copy2(self.filepath, backup)
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump({}, f)
                logger.info("All FSM states cleared on restart (backup saved)")
            except Exception as e:
                logger.error(f"Failed to clear FSM states: {e}")
    async def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass
