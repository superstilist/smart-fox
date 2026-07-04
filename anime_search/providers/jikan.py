from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from anime_search.providers.base import AnimeProvider, ProviderNoResult, extract_image

log = logging.getLogger(__name__)


class JikanProvider(AnimeProvider):
    name = "jikan"
    confidence = 0.98
    priority = 10

    async def _jikan_get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.client.get(url, params=params or {})
                if response.status_code == 429:
                    delay = 1.0 * (attempt + 1)
                    log.warning("Jikan rate limited (429), waiting %.1fs", delay)
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    delay = 1.0 * (attempt + 1)
                    log.warning("Jikan rate limited (429), waiting %.1fs", delay)
                    await asyncio.sleep(delay)
                    continue
                last_exc = exc
                break
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5)
                    continue
                break
            except Exception as exc:
                last_exc = exc
                break
        raise last_exc or RuntimeError("Jikan request failed after retries")

    async def fetch(self, query: str, content_filter: str = "sfw") -> dict[str, Any]:
        base = self.settings.jikan_base_url
        sfw_param = "true" if content_filter == "sfw" else "false"
        search = await self._jikan_get(f"{base}/anime", {"q": query, "limit": 1, "sfw": sfw_param})
        anime_list = search.get("data") or []
        anime = anime_list[0] if anime_list else {}
        mal_id = anime.get("mal_id")

        if not mal_id:
            raise ProviderNoResult(self.name, query)

        endpoints = {
            "details": f"{base}/anime/{mal_id}/full",
            "characters": f"{base}/anime/{mal_id}/characters",
            "staff": f"{base}/anime/{mal_id}/staff",
            "relations": f"{base}/anime/{mal_id}/relations",
            "recommendations": f"{base}/anime/{mal_id}/recommendations",
        }

        results: dict[str, Any] = {}
        for key, url in endpoints.items():
            try:
                results[key] = await self._jikan_get(url)
            except Exception as exc:
                log.debug("Jikan %s failed for %d: %s", key, mal_id, exc)
                results[key] = {}

        return {
            "search": search,
            "details": results.get("details", {}),
            "characters": results.get("characters", {}),
            "staff": results.get("staff", {}),
            "relations": results.get("relations", {}),
            "recommendations": results.get("recommendations", {}),
        }

    def normalize(self, query: str, raw: dict[str, Any]) -> dict[str, Any]:
        anime = (
            raw.get("details", {}).get("data")
            or (raw.get("search", {}).get("data") or [{}])[0]
            or {}
        )
        if not anime:
            return {}

        images = anime.get("images", {})
        poster = extract_image(images)
        trailer = anime.get("trailer") or {}
        title_synonyms = anime.get("title_synonyms") or []

        titles = []
        for t in [anime.get("title"), anime.get("title_english"), anime.get("title_japanese"), *title_synonyms]:
            if t:
                titles.append(t)

        characters = []
        for item in raw.get("characters", {}).get("data", [])[:30]:
            character = item.get("character") or {}
            char_images = character.get("images", {})
            voice_actors = [
                {
                    "name": va.get("person", {}).get("name"),
                    "language": va.get("language"),
                }
                for va in item.get("voice_actors", [])
            ]
            characters.append(
                {
                    "name": character.get("name"),
                    "role": item.get("role"),
                    "image": extract_image(char_images),
                    "voice_actors": voice_actors,
                    "source": self.name,
                }
            )

        return {
            "titles": {"all": titles},
            "description": {
                "summary": anime.get("synopsis"),
                "background": anime.get("background"),
            },
            "genres": [x.get("name") for x in anime.get("genres", []) if x.get("name")],
            "themes": [x.get("name") for x in anime.get("themes", []) if x.get("name")],
            "demographics": [x.get("name") for x in anime.get("demographics", []) if x.get("name")],
            "studios": [x.get("name") for x in anime.get("studios", []) if x.get("name")],
            "producers": [x.get("name") for x in anime.get("producers", []) if x.get("name")],
            "characters": characters,
            "staff": [
                {
                    "name": item.get("person", {}).get("name"),
                    "positions": item.get("positions", []),
                    "source": self.name,
                }
                for item in raw.get("staff", {}).get("data", [])[:30]
            ],
            "media": {
                "poster": poster,
                "trailer": trailer.get("url"),
                "trailer_embed": trailer.get("embed_url"),
            },
            "release": {
                "episodes": anime.get("episodes"),
                "status": anime.get("status"),
                "airing": anime.get("airing"),
                "aired": anime.get("aired"),
                "rating": anime.get("rating"),
                "type": anime.get("type"),
                "source": anime.get("source"),
                "duration": anime.get("duration"),
            },
            "statistics": {
                "score": anime.get("score"),
                "scored_by": anime.get("scored_by"),
                "popularity": anime.get("popularity"),
                "rank": anime.get("rank"),
                "members": anime.get("members"),
                "favorites": anime.get("favorites"),
            },
            "relationships": raw.get("relations", {}).get("data", []),
            "recommendations": [
                {
                    "title": item.get("entry", {}).get("title"),
                    "url": item.get("entry", {}).get("url"),
                    "source": self.name,
                }
                for item in raw.get("recommendations", {}).get("data", [])[:20]
            ],
        }
