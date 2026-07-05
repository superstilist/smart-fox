from __future__ import annotations

import json
import time
import uuid
import re
from pathlib import Path
from typing import Any

LIBRARY_FILE = Path(".library.json")

STATUSES = ("watching", "completed", "plan_to_watch", "dropped", "on_hold")
SORT_KEYS = ("added_at", "updated_at", "title", "rating", "score", "progress", "status")


def _load() -> dict[str, Any]:
    if LIBRARY_FILE.is_file():
        try:
            with open(LIBRARY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "entries" in data:
                _migrate_legacy_ids(data)
                return data
        except Exception:
            pass
    return {"entries": [], "stats": _compute_stats([])}


def _save(data: dict[str, Any]) -> None:
    data["stats"] = _compute_stats(data["entries"])
    with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _compute_stats(entries: list[dict[str, Any]]) -> dict[str, int]:
    stats: dict[str, int] = {
        "total": len(entries),
        "watching": 0,
        "completed": 0,
        "plan_to_watch": 0,
        "dropped": 0,
        "on_hold": 0,
    }
    for e in entries:
        s = e.get("status", "plan_to_watch")
        if s in stats:
            stats[s] += 1
    return stats


def _make_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return f"{slug}_{uuid.uuid4().hex[:8]}"


def _migrate_legacy_ids(data: dict[str, Any]) -> None:
    entries = data.get("entries", [])
    seen: set[str] = set()
    changed = False
    for e in entries:
        eid = e.get("id", "")
        if eid in seen or not eid:
            e["id"] = _make_id(e.get("title", "unknown"))
            seen.add(e["id"])
            changed = True
        else:
            seen.add(eid)


def _normalize_entry(data: dict[str, Any]) -> dict[str, Any]:
    title = data.get("title", "").strip()
    if not title:
        return {}
    status = data.get("status", "plan_to_watch")
    if status not in STATUSES:
        status = "plan_to_watch"
    rating = data.get("rating")
    if rating is not None:
        try:
            rating = round(max(0.0, min(10.0, float(rating))), 1)
        except (TypeError, ValueError):
            rating = None
    progress = 0
    try:
        progress = max(0, int(data.get("progress", 0)))
    except (TypeError, ValueError):
        pass
    total_episodes = data.get("total_episodes") or data.get("episodes")
    try:
        total_episodes = int(total_episodes) if total_episodes else None
    except (TypeError, ValueError):
        total_episodes = None
    return {
        "id": data.get("id") or _make_id(title),
        "title": title,
        "title_english": (data.get("title_english") or "").strip(),
        "title_japanese": (data.get("title_japanese") or "").strip(),
        "poster": data.get("poster", ""),
        "banner": data.get("banner", ""),
        "score": data.get("score"),
        "episodes": data.get("episodes"),
        "type": data.get("type", "TV"),
        "status": status,
        "rating": rating,
        "progress": progress,
        "total_episodes": total_episodes,
        "notes": data.get("notes", ""),
        "genres": data.get("genres", []),
        "themes": data.get("themes", []),
        "url": data.get("url", ""),
        "mal_id": data.get("mal_id"),
        "added_at": data.get("added_at") or time.time(),
        "updated_at": time.time(),
    }


def _sort_entries(entries: list[dict[str, Any]], sort_by: str, desc: bool) -> list[dict[str, Any]]:
    if sort_by not in SORT_KEYS:
        sort_by = "added_at"
    reverse = desc
    if sort_by in ("title", "status"):
        entries.sort(key=lambda e: (e.get(sort_by) or "").lower(), reverse=reverse)
    else:
        entries.sort(key=lambda e: e.get(sort_by) or 0, reverse=reverse)
    return entries


def get_library() -> dict[str, Any]:
    data = _load()
    return {
        "entries": data["entries"],
        "stats": _compute_stats(data["entries"]),
    }


def get_sorted_library(sort_by: str = "added_at", desc: bool = True) -> dict[str, Any]:
    data = _load()
    entries = _sort_entries(list(data["entries"]), sort_by, desc)
    return {
        "entries": entries,
        "stats": _compute_stats(data["entries"]),
        "sort": sort_by,
        "desc": desc,
    }


def add_entry(anime_data: dict[str, Any]) -> dict[str, Any]:
    entry = _normalize_entry(anime_data)
    if not entry:
        return {"error": "Title is required"}
    data = _load()
    existing_titles = {e["title"].lower() for e in data["entries"]}
    if entry["title"].lower() in existing_titles:
        return {"error": f"'{entry['title']}' is already in your library"}
    data["entries"].append(entry)
    _save(data)
    return {"success": True, "entry": entry, "stats": _compute_stats(data["entries"])}


def add_or_update_entry(anime_data: dict[str, Any]) -> dict[str, Any]:
    title = anime_data.get("title", "").strip()
    if not title:
        return {"error": "Title is required"}
    data = _load()
    for e in data["entries"]:
        if e["title"].lower() == title.lower():
            if "status" in anime_data and anime_data["status"] in STATUSES:
                e["status"] = anime_data["status"]
            if "rating" in anime_data:
                try:
                    e["rating"] = round(max(0.0, min(10.0, float(anime_data["rating"]))), 1)
                except (TypeError, ValueError):
                    pass
            if "progress" in anime_data:
                try:
                    e["progress"] = max(0, int(anime_data["progress"]))
                except (TypeError, ValueError):
                    pass
            if "notes" in anime_data:
                e["notes"] = anime_data["notes"]
            for key in ("poster", "banner", "score", "episodes", "type", "genres", "themes", "url", "mal_id"):
                if key in anime_data and anime_data[key]:
                    e[key] = anime_data[key]
            e["updated_at"] = time.time()
            _save(data)
            return {"success": True, "entry": e, "stats": _compute_stats(data["entries"])}
    return add_entry(anime_data)


def update_entry(entry_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    data = _load()
    for e in data["entries"]:
        if e["id"] == entry_id:
            if "status" in updates and updates["status"] in STATUSES:
                e["status"] = updates["status"]
            if "rating" in updates:
                try:
                    e["rating"] = round(max(0.0, min(10.0, float(updates["rating"]))), 1)
                except (TypeError, ValueError):
                    pass
            if "progress" in updates:
                try:
                    e["progress"] = max(0, int(updates["progress"]))
                except (TypeError, ValueError):
                    pass
            if "notes" in updates:
                e["notes"] = updates["notes"]
            e["updated_at"] = time.time()
            _save(data)
            return {"success": True, "entry": e, "stats": _compute_stats(data["entries"])}
    return {"error": "Entry not found"}


def remove_entry(entry_id: str) -> dict[str, Any]:
    data = _load()
    original = len(data["entries"])
    data["entries"] = [e for e in data["entries"] if e["id"] != entry_id]
    if len(data["entries"]) < original:
        _save(data)
        return {"success": True, "stats": _compute_stats(data["entries"])}
    return {"error": "Entry not found"}


def bulk_remove(entry_ids: list[str]) -> dict[str, Any]:
    data = _load()
    remove_set = set(entry_ids)
    original = len(data["entries"])
    data["entries"] = [e for e in data["entries"] if e["id"] not in remove_set]
    removed = original - len(data["entries"])
    if removed:
        _save(data)
    return {"success": True, "removed": removed, "stats": _compute_stats(data["entries"])}


def bulk_update_status(entry_ids: list[str], status: str) -> dict[str, Any]:
    if status not in STATUSES:
        return {"error": f"Invalid status: {status}"}
    data = _load()
    update_set = set(entry_ids)
    count = 0
    for e in data["entries"]:
        if e["id"] in update_set:
            e["status"] = status
            e["updated_at"] = time.time()
            count += 1
    if count:
        _save(data)
    return {"success": True, "updated": count, "stats": _compute_stats(data["entries"])}


def get_entry(entry_id: str) -> dict[str, Any] | None:
    data = _load()
    for e in data["entries"]:
        if e["id"] == entry_id:
            return e
    return None


def search_library(query: str, status: str = "") -> list[dict[str, Any]]:
    data = _load()
    entries = data["entries"]
    if status and status in STATUSES:
        entries = [e for e in entries if e.get("status") == status]
    if query:
        q = query.lower()
        entries = [
            e for e in entries
            if q in e.get("title", "").lower()
            or q in e.get("title_english", "").lower()
            or q in e.get("title_japanese", "").lower()
            or any(q in g.lower() for g in e.get("genres", []))
            or any(q in t.lower() for t in e.get("themes", []))
        ]
    return entries


def export_library() -> dict[str, Any]:
    data = _load()
    return {
        "version": 1,
        "exported_at": time.time(),
        "entries": data["entries"],
        "stats": _compute_stats(data["entries"]),
    }


def import_library(payload: dict[str, Any], mode: str = "merge") -> dict[str, Any]:
    if mode not in ("merge", "replace"):
        mode = "merge"
    incoming = payload.get("entries", [])
    if not isinstance(incoming, list):
        return {"error": "Invalid import data: entries must be a list"}
    data = _load()
    if mode == "replace":
        data["entries"] = []
    existing_titles = {e["title"].lower() for e in data["entries"]}
    existing_ids = {e["id"] for e in data["entries"]}
    imported = 0
    skipped = 0
    errors = []
    for raw in incoming:
        entry = _normalize_entry(raw)
        if not entry:
            skipped += 1
            continue
        if entry["title"].lower() in existing_titles:
            skipped += 1
            continue
        if entry["id"] in existing_ids:
            entry["id"] = _make_id(entry["title"])
        existing_titles.add(entry["title"].lower())
        existing_ids.add(entry["id"])
        data["entries"].append(entry)
        imported += 1
    _save(data)
    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "stats": _compute_stats(data["entries"]),
    }
