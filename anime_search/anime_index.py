from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "anime_index.db"


class AnimeIndex:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS anime (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    title_english TEXT,
                    title_japanese TEXT,
                    mal_id INTEGER,
                    anilist_id INTEGER,
                    score REAL,
                    episodes INTEGER,
                    status TEXT,
                    type TEXT,
                    genres TEXT,
                    themes TEXT,
                    studios TEXT,
                    synopsis TEXT,
                    poster TEXT,
                    url TEXT,
                    popularity INTEGER,
                    created_at REAL DEFAULT (strftime('%s','now')),
                    UNIQUE(title)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_title ON anime(title)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON anime(score DESC)")

    def add(self, data: dict[str, Any]) -> int:
        title = data.get("title", "").strip()
        if not title:
            return 0
        with sqlite3.connect(str(self.db_path)) as conn:
            existing = conn.execute(
                "SELECT id FROM anime WHERE title = ?", (title,)
            ).fetchone()
            if existing:
                return existing[0]
            cursor = conn.execute(
                """INSERT OR IGNORE INTO anime
                (title, title_english, title_japanese, mal_id, anilist_id,
                 score, episodes, status, type, genres, themes, studios,
                 synopsis, poster, url, popularity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    title,
                    data.get("title_english", ""),
                    data.get("title_japanese", ""),
                    data.get("mal_id"),
                    data.get("anilist_id"),
                    data.get("score"),
                    data.get("episodes"),
                    data.get("status", ""),
                    data.get("type", ""),
                    json.dumps(data.get("genres", [])),
                    json.dumps(data.get("themes", [])),
                    json.dumps(data.get("studios", [])),
                    data.get("synopsis", "")[:500],
                    data.get("poster", ""),
                    data.get("url", ""),
                    data.get("popularity", 0),
                ),
            )
            return cursor.lastrowid or 0

    def add_batch(self, items: list[dict[str, Any]]) -> dict[str, int]:
        result = {}
        for item in items:
            idx = self.add(item)
            if idx:
                result[item.get("title", "")] = idx
        return result

    def get(self, anime_id: int) -> dict[str, Any] | None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM anime WHERE id = ?", (anime_id,)).fetchone()
            if not row:
                return None
            return dict(row)

    def get_by_title(self, title: str) -> dict[str, Any] | None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM anime WHERE title = ?", (title,)).fetchone()
            if not row:
                return None
            return dict(row)

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM anime
                WHERE title LIKE ? OR title_english LIKE ? OR genres LIKE ?
                ORDER BY score DESC NULLS LAST, popularity DESC NULLS LAST
                LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_top(self, limit: int = 50) -> list[dict[str, Any]]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM anime
                WHERE score IS NOT NULL
                ORDER BY score DESC
                LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM anime WHERE id IN ({placeholders})", ids
            ).fetchall()
            return [dict(r) for r in rows]

    def get_compact(self, ids: list[int]) -> str:
        items = self.get_by_ids(ids)
        lines = []
        for item in items:
            genres = json.loads(item.get("genres") or "[]")
            lines.append(
                f"[{item['id']}]{item['title']} | {item.get('score') or '?'} | {','.join(genres[:3])}"
            )
        return "\n".join(lines)

    def count(self) -> int:
        with sqlite3.connect(str(self.db_path)) as conn:
            return conn.execute("SELECT COUNT(*) FROM anime").fetchone()[0]

    def get_all_titles_index(self) -> str:
        with sqlite3.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                "SELECT id, title, score FROM anime ORDER BY score DESC NULLS LAST"
            ).fetchall()
            lines = [f"[{r[0]}] {r[1]} ({r[2] or '?'})" for r in rows]
            return "\n".join(lines)


_anime_index: AnimeIndex | None = None


def get_anime_index() -> AnimeIndex:
    global _anime_index
    if _anime_index is None:
        _anime_index = AnimeIndex()
    return _anime_index
