from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CACHE_VERSION = "v3"

SEARCH_TTL = 1800
RECOMMENDATION_TTL = 3600
PROVIDER_TTL = 1800


class InMemoryCache:
    def __init__(self, max_size: int = 512) -> None:
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, expires_at = entry
            if expires_at < time.time():
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            if key in self._store:
                del self._store[key]
            elif len(self._store) >= self._max_size:
                self._store.popitem(last=False)
            self._store[key] = (value, time.time() + ttl)
            self._store.move_to_end(key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            valid = sum(1 for _, (_, exp) in self._store.items() if exp > now)
            return {
                "size": len(self._store),
                "max_size": self._max_size,
                "valid_entries": valid,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / max(self.hits + self.misses, 1) * 100, 1),
            }


class SQLiteJsonCache:
    def __init__(self, path: Path, ttl_seconds: int) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._l1 = InMemoryCache(max_size=256)
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

    def _l1_key(self, namespace: str, key: str, version: str) -> str:
        return f"{namespace}:{key}:{version}"

    def get(self, namespace: str, key: str, version: str | None = None) -> Any | None:
        ver = version or CACHE_VERSION
        l1_key = self._l1_key(namespace, key, ver)
        cached = self._l1.get(l1_key)
        if cached is not None:
            return cached

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
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
        ttl_left = int(expires_at - now)
        if ttl_left > 0:
            self._l1.set(l1_key, data, ttl_left)
        return data

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
        l1_key = self._l1_key(namespace, key, ver)
        self._l1.set(l1_key, payload, ttl)

    def delete(self, namespace: str, key: str, version: str | None = None) -> None:
        ver = version or CACHE_VERSION
        with self._connect() as con:
            con.execute(
                "DELETE FROM cache_entries WHERE namespace = ? AND cache_key = ? AND version = ?",
                (namespace, key, ver),
            )
        self._l1.delete(self._l1_key(namespace, key, ver))

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._connect() as con:
            cursor = con.execute("DELETE FROM cache_entries WHERE expires_at < ?", (now,))
            return cursor.rowcount

    def clear_namespace(self, namespace: str) -> int:
        with self._connect() as con:
            cursor = con.execute("DELETE FROM cache_entries WHERE namespace = ?", (namespace,))
        self._l1.clear()
        return cursor.rowcount

    def get_stats(self) -> dict[str, Any]:
        now = time.time()
        with self._connect() as con:
            total = con.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
            valid = con.execute("SELECT COUNT(*) FROM cache_entries WHERE expires_at > ?", (now,)).fetchone()[0]
            expired = total - valid
        return {
            "l1": self._l1.stats(),
            "l2_total": total,
            "l2_valid": valid,
            "l2_expired": expired,
        }
