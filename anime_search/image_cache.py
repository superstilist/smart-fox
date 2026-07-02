from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

from anime_search.cache import SQLiteJsonCache
from anime_search.models import UnifiedAnimeProfile


def image_cache_path(cache_db_path: Path) -> Path:
    return cache_db_path.parent / "images"


def extension_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".img"


async def cache_image(
    client: httpx.AsyncClient,
    cache: SQLiteJsonCache,
    image_dir: Path,
    url: str,
) -> str | None:
    cached = cache.get("images", url)
    if cached and Path(cached.get("path", "")).exists():
        return cached["path"]

    image_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    target = image_dir / f"{digest}{extension_from_url(url)}"
    try:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            return None
        target.write_bytes(response.content)
        cache.set("images", url, {"url": url, "path": str(target), "content_type": content_type})
        return str(target)
    except Exception:  # noqa: BLE001 - image cache should never break search.
        return None


async def cache_profile_images(
    client: httpx.AsyncClient,
    cache: SQLiteJsonCache,
    profile: UnifiedAnimeProfile,
    cache_db_path: Path,
) -> None:
    image_dir = image_cache_path(cache_db_path)
    media_urls = {
        key: value
        for key, value in profile.media.items()
        if isinstance(value, str) and value.startswith(("http://", "https://"))
    }
    character_pairs = [
        (index, character.get("image"))
        for index, character in enumerate(profile.characters[:50])
        if isinstance(character.get("image"), str)
    ]

    media_tasks = {
        key: asyncio.create_task(cache_image(client, cache, image_dir, url))
        for key, url in media_urls.items()
    }
    character_tasks = {
        index: asyncio.create_task(cache_image(client, cache, image_dir, url))
        for index, url in character_pairs
    }

    cached_media = {
        key: path
        for key, task in media_tasks.items()
        if (path := await task)
    }
    for index, task in character_tasks.items():
        path = await task
        if path:
            profile.characters[index]["image_cached_path"] = path
    if cached_media:
        profile.media["cached_paths"] = cached_media

