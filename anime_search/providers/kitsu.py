from __future__ import annotations

import logging
from typing import Any

import httpx

from anime_search.providers.base import AnimeProvider, ProviderNoResult

log = logging.getLogger(__name__)


class KitsuProvider(AnimeProvider):
    name = "kitsu"
    confidence = 0.95
    priority = 30

    async def fetch(self, query: str, content_filter: str = "sfw") -> dict[str, Any]:
        headers = {"Accept": "application/vnd.api+json"}
        params: dict[str, Any] = {"filter[text]": query, "page[limit]": 1}
        response = await self.client.get(
            f"{self.settings.kitsu_base_url}/anime",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        anime_list = data.get("data") or []
        anime = anime_list[0] if anime_list else {}
        anime_id = anime.get("id")

        if not anime_id:
            raise ProviderNoResult(self.name, query)

        characters: dict[str, Any] = {}
        try:
            char_response = await self.client.get(
                f"{self.settings.kitsu_base_url}/anime/{anime_id}/anime-characters",
                params={"include": "character,person", "page[limit]": 30},
                headers=headers,
            )
            if char_response.status_code == 200:
                characters = char_response.json()
        except Exception as exc:
            log.debug("Kitsu character fetch failed for %s: %s", anime_id, exc)

        return {"search": data, "characters": characters}

    def normalize(self, query: str, raw: dict[str, Any]) -> dict[str, Any]:
        anime = (raw.get("search", {}).get("data") or [{}])[0]
        if not anime:
            return {}

        attrs = anime.get("attributes") or {}
        titles = attrs.get("titles") or {}
        poster = attrs.get("posterImage") or {}
        cover = attrs.get("coverImage") or {}

        included = raw.get("characters", {}).get("included", [])
        included_by_key = {(item.get("type"), item.get("id")): item for item in included}

        characters = []
        for item in raw.get("characters", {}).get("data", [])[:30]:
            rel = item.get("relationships", {}).get("character", {}).get("data") or {}
            character = included_by_key.get((rel.get("type"), rel.get("id")), {})
            cattrs = character.get("attributes") or {}
            image = cattrs.get("image") or {}
            characters.append(
                {
                    "name": cattrs.get("name") or cattrs.get("canonicalName"),
                    "role": (item.get("attributes") or {}).get("role"),
                    "description": cattrs.get("description"),
                    "image": image.get("original") or image.get("large"),
                    "source": self.name,
                }
            )

        all_titles = [attrs.get("canonicalTitle")]
        all_titles.extend(titles.values())

        return {
            "titles": {"all": [t for t in all_titles if t]},
            "description": {"summary": attrs.get("synopsis")},
            "genres": [],
            "themes": [],
            "characters": characters,
            "media": {
                "poster": poster.get("original") or poster.get("large"),
                "banner": cover.get("original") or cover.get("large"),
            },
            "release": {
                "episodes": attrs.get("episodeCount"),
                "status": attrs.get("status"),
                "start_date": attrs.get("startDate"),
                "end_date": attrs.get("endDate"),
                "type": attrs.get("showType"),
            },
            "statistics": {
                "average_rating": attrs.get("averageRating"),
                "popularity_rank": attrs.get("popularityRank"),
                "rating_rank": attrs.get("ratingRank"),
            },
        }
