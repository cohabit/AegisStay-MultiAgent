"""Small-hotel persistence boundary.

SQLite is intentionally kept behind this module so the business engine remains
usable in offline tests.  A deployment enables it with ``AEGIS_DB_PATH``.  The
single-process API service is the writer; SQLite WAL and a busy timeout make
operator reads safe while a request is being committed.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class SQLiteStateStore:
    """Persist shared hotel state and resumable session context as JSON rows."""

    def __init__(self, path: str) -> None:
        if not path or path == ":memory:":
            raise ValueError("SQLite 持久化需要文件路径")
        self.path = str(Path(path).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=10000")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS aegis_business_state (
                    state_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS aegis_sessions (
                    session_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    client_type TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _decode(value: str) -> Any:
        return json.loads(value)

    def load_business(self) -> dict[str, Any] | None:
        with self._lock:
            rows = self._connection.execute(
                "SELECT state_key, value_json FROM aegis_business_state"
            ).fetchall()
        if not rows:
            return None
        return {row["state_key"]: self._decode(row["value_json"]) for row in rows}

    def save_business(self, state: dict[str, Any]) -> None:
        now = time.time()
        with self._lock, self._connection:
            self._connection.executemany(
                "INSERT INTO aegis_business_state(state_key, value_json, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(state_key) DO UPDATE SET "
                "value_json=excluded.value_json, updated_at=excluded.updated_at",
                [(key, self._encode(value), now) for key, value in state.items()],
            )

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT state_json, client_type, ttl_seconds FROM aegis_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        state = self._decode(row["state_json"])
        state["client_type"] = row["client_type"]
        state["ttl_seconds"] = row["ttl_seconds"]
        return state

    def save_session(self, session_id: str, state: dict[str, Any], client_type: str, ttl_seconds: int) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO aegis_sessions(session_id, state_json, client_type, ttl_seconds, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "state_json=excluded.state_json, client_type=excluded.client_type, "
                "ttl_seconds=excluded.ttl_seconds, updated_at=excluded.updated_at",
                (session_id, self._encode(state), client_type, ttl_seconds, time.time()),
            )

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM aegis_sessions WHERE session_id=?", (session_id,))

    def close(self) -> None:
        with self._lock:
            self._connection.close()
