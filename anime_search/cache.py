from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CACHE_VERSION = "v2"


class SQLiteJsonCache:
    def __init__(self, path: Path, ttl_seconds: int) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            try:
                con.execute("SELECT version FROM cache_entries LIMIT 1")
            except sqlite3.OperationalError:
                con.execute("DROP TABLE IF EXISTS cache_entries")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    version TEXT NOT NULL DEFAULT 'v1',
                    PRIMARY KEY (namespace, cache_key, version)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries(expires_at)"
            )

    def get(self, namespace: str, key: str, version: str | None = None) -> Any | None:
        ver = version or CACHE_VERSION
        now = time.time()
        with self._connect() as con:
            row = con.execute(
                "SELECT payload, expires_at FROM cache_entries WHERE namespace = ? AND cache_key = ? AND version = ?",
                (namespace, key, ver),
            ).fetchone()
        if not row:
            return None
        payload, expires_at = row
        if expires_at < now:
            self.delete(namespace, key, ver)
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    def set(self, namespace: str, key: str, payload: Any, ttl_seconds: int | None = None, version: str | None = None) -> None:
        ver = version or CACHE_VERSION
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        try:
            data = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            return
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO cache_entries(namespace, cache_key, payload, created_at, expires_at, version)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (namespace, key, data, now, now + ttl, ver),
            )

    def delete(self, namespace: str, key: str, version: str | None = None) -> None:
        ver = version or CACHE_VERSION
        with self._connect() as con:
            con.execute(
                "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ? AND version = ?",
                (namespace, key, ver),
            )

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._connect() as con:
            cursor = con.execute("DELETE FROM cache_entries WHERE expires_at < ?", (now,))
            return cursor.rowcount

    def clear_namespace(self, namespace: str) -> int:
        with self._connect() as con:
            cursor = con.execute("DELETE FROM cache_entries WHERE namespace = ?", (namespace,))
            return cursor.rowcount

    def get_stats(self) -> dict[str, Any]:
        now = time.time()
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
            valid = con.execute("SELECT COUNT(*) FROM cache_entries WHERE expires_at > ?", (now,)).fetchone()[0]
            expired = total - valid
        return {"total": total, "valid": valid, "expired": expired}
