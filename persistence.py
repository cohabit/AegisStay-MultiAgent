"""Atomic SQLite checkpoints. Exactly one service owns business writes."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class StorageConflict(RuntimeError):
    pass


class SQLiteStateStore:
    def __init__(self, path: str) -> None:
        if not path or path == ":memory:":
            raise ValueError("持久化需要文件路径")
        self.path = str(Path(path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._writer = open(self.path + ".writer.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if Path(self.path + ".writer.lock").stat().st_size == 0:
                    self._writer.write(b"0")
                    self._writer.flush()
                self._writer.seek(0)
                msvcrt.locking(self._writer.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._writer.close()
            raise StorageConflict("数据库已有业务写入进程；工作台必须通过 API 访问，API 只能运行一个 worker") from error
        try:
            self._connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA busy_timeout=10000")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS aegis_business_state (
                    state_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS aegis_sessions (
                    session_id TEXT PRIMARY KEY, state_json TEXT NOT NULL,
                    client_type TEXT NOT NULL, ttl_seconds INTEGER NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS aegis_revision (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO aegis_revision VALUES (1, 0);
                CREATE TABLE IF NOT EXISTS aegis_runs (
                    run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, data_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS aegis_runs_session ON aegis_runs(session_id);
                CREATE TABLE IF NOT EXISTS aegis_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, detail_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aegis_messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE NOT NULL,
                    session_id TEXT NOT NULL, data_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS aegis_messages_session ON aegis_messages(session_id, sequence);
            """)
            self._revision = self._connection.execute("SELECT revision FROM aegis_revision WHERE id=1").fetchone()[0]
        except Exception:
            self._writer.close()
            raise

    @staticmethod
    def _encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def load_business(self) -> dict[str, Any] | None:
        with self._lock:
            rows = self._connection.execute("SELECT state_key,value_json FROM aegis_business_state").fetchall()
        return {r["state_key"]: json.loads(r["value_json"]) for r in rows} if rows else None

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM aegis_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        return {**json.loads(row["state_json"]), "client_type": row["client_type"],
                "ttl_seconds": row["ttl_seconds"], "updated_at": row["updated_at"]}

    def session_ids(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._connection.execute("SELECT session_id FROM aegis_sessions")]

    def load_runs(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [json.loads(r[0]) for r in self._connection.execute(
                "SELECT data_json FROM aegis_runs WHERE session_id=? ORDER BY rowid", (session_id,))]

    def find_run(self, session_id: str, *, run_id: str | None = None, request_id: str | None = None) -> dict | None:
        for run in self.load_runs(session_id):
            if run_id and run["run_id"] == run_id or request_id and run.get("request_id") == request_id:
                return run
        return None

    def checkpoint(self, business: dict[str, Any], sessions: list[dict[str, Any]],
                   runs: list[dict[str, Any]], deleted: tuple[str, ...] = (),
                   event: dict[str, Any] | None = None) -> None:
        """Business, context, retry records and audit commit or roll back together."""
        now = time.time()
        business_rows = [(k, self._encode(v), now) for k, v in business.items()]
        session_rows = [(s["session_id"], self._encode(s["state"]), s["client_type"],
                         s["ttl_seconds"], s["updated_at"]) for s in sessions]
        run_rows = [(r["run_id"], r["session_id"], self._encode(r)) for r in runs]
        with self._lock, self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            cursor = self._connection.execute(
                "UPDATE aegis_revision SET revision=revision+1 WHERE id=1 AND revision=?", (self._revision,))
            if cursor.rowcount != 1:
                raise StorageConflict("存储版本已变化，拒绝覆盖其他写入")
            self._connection.executemany(
                "INSERT INTO aegis_business_state VALUES(?,?,?) ON CONFLICT(state_key) DO UPDATE SET "
                "value_json=excluded.value_json,updated_at=excluded.updated_at", business_rows)
            self._connection.executemany(
                "INSERT INTO aegis_sessions VALUES(?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "state_json=excluded.state_json,client_type=excluded.client_type,ttl_seconds=excluded.ttl_seconds,"
                "updated_at=excluded.updated_at", session_rows)
            self._connection.executemany(
                "INSERT INTO aegis_runs VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET data_json=excluded.data_json", run_rows)
            self._connection.executemany(
                "INSERT OR IGNORE INTO aegis_messages(message_id,session_id,data_json) VALUES(?,?,?)",
                [(m["message_id"], s["session_id"], self._encode(m)) for s in sessions for m in s["state"]["messages"]])
            for sid in deleted:
                self._connection.execute("DELETE FROM aegis_sessions WHERE session_id=?", (sid,))
                self._connection.execute("DELETE FROM aegis_runs WHERE session_id=?", (sid,))
                self._connection.execute("DELETE FROM aegis_messages WHERE session_id=?", (sid,))
            if event:
                self._connection.execute("INSERT INTO aegis_audit(timestamp,actor,action,target,detail_json) VALUES(?,?,?,?,?)",
                                         (now, event["actor"], event["action"], event.get("target", ""), self._encode(event.get("detail", {}))))
        self._revision += 1

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM aegis_audit ORDER BY sequence DESC LIMIT ?", (min(500, max(1, limit)),)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in rows]

    def messages(self, session_id: str, before: int | None = None, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sequence,data_json FROM aegis_messages WHERE session_id=? AND sequence<? "
                "ORDER BY sequence DESC LIMIT ?", (session_id, before if before is not None else 2**63 - 1, min(100, max(1, limit)))).fetchall()
        return [{"sequence": r["sequence"], **json.loads(r["data_json"])} for r in reversed(rows)]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            self._connection.close()
            if os.name == "nt":
                import msvcrt
                self._writer.seek(0)
                msvcrt.locking(self._writer.fileno(), msvcrt.LK_UNLCK, 1)
            self._writer.close()
