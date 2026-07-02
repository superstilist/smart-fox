from __future__ import annotations

import re
from typing import Any

import httpx

from anime_search.providers.base import AnimeProvider, ProviderNoResult, compact_html

ANILIST_SEARCH_QUERY = """
query AnimeSearch($search: String, $genre_not_in: [String], $isAdult: Boolean) {
  Page(perPage: 1) {
    media(search: $search, type: ANIME, genre_not_in: $genre_not_in, isAdult: $isAdult, sort: POPULARITY_DESC) {
      id
      idMal
      title { romaji english native userPreferred }
      coverImage { large }
      description(asHtml: false)
      genres
      episodes
      status
      format
      startDate { year month day }
      averageScore
      meanScore
      popularity
      studios(isMain: true) { nodes { name } }
      characters(sort: ROLE, perPage: 10) {
        edges {
          role
          node { name { full } image { large } }
        }
      }
      recommendations(perPage: 5) {
        nodes { mediaRecommendation { title { userPreferred } siteUrl averageScore } }
      }
      siteUrl
    }
  }
}
"""

NSFW_ANILIST_GENRES = {"Ecchi", "Hentai", "Erotica"}


def normalize_query(raw_query: str) -> str:
    query = raw_query.strip().lower()
    query = re.sub(r"\s+", " ", query).strip()

    typo_map = {
        r"\baniem\b": "anime",
        r"\banme\b": "anime",
        r"\bromnce\b": "romance",
        r"\bschoo\b": "school",
        r"\bschol\b": "school",
        r"\bteh\b": "the",
        r"\babou\b": "about",
        r"\babot\b": "about",
        r"\babut\b": "about",
        r"\bstudnt\b": "student",
        r"\bstuden\b": "student",
        r"\bstudenta\b": "student",
        r"\btcher\b": "teacher",
        r"\bteaher\b": "teacher",
        r"\becchii\b": "ecchi",
        r"\becch\b": "ecchi",
    }
    for pattern, replacement in typo_map.items():
        query = re.sub(pattern, replacement, query)

    stop_phrases = [
        (r"\babout teacher and her student\b", "teacher student"),
        (r"\babout teacher and his student\b", "teacher student"),
        (r"\babout teacher and student\b", "teacher student"),
        (r"\babout teacher\b", "teacher"),
        (r"\babout\b", ""),
        (r"\banime like\b", ""),
        (r"\banime similar to\b", ""),
        (r"\bsimilar to\b", ""),
        (r"\blike\b", ""),
        (r"\bwatch\b", ""),
        (r"\brecommend\b", ""),
        (r"\bfind\b", ""),
        (r"\bsearch\b", ""),
        (r"\blook for\b", ""),
    ]
    for pattern, replacement in stop_phrases:
        query = re.sub(pattern, replacement, query)

    query = re.sub(r"[^a-z0-9\s\-:]", " ", query)
    query = re.sub(r"\s+", " ", query).strip()

    if query.endswith(" anime"):
        pass
    elif not any(w in query for w in ["anime", "manga", "ova", "movie", "series"]):
        query = f"{query} anime"

    return query


class AniListProvider(AnimeProvider):
    name = "anilist"
    confidence = 1.0

    async def fetch(self, query: str, content_filter: str = "sfw") -> dict[str, Any]:
        normalized = normalize_query(query)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        variables: dict[str, Any] = {"search": normalized}

        if content_filter == "sfw":
            variables["genre_not_in"] = list(NSFW_ANILIST_GENRES)
            variables["isAdult"] = False
        elif content_filter == "nsfw":
            variables["isAdult"] = None

        payload = {
            "query": ANILIST_SEARCH_QUERY,
            "variables": variables,
        }

        response = await self.client.post(
            self.settings.anilist_base_url,
            json=payload,
            headers=headers,
        )

        if response.status_code == 404:
            raise ProviderNoResult(self.name, query)

        response.raise_for_status()

        data = response.json()

        errors = data.get("errors") or []
        if errors:
            error_messages = [e.get("message", "") for e in errors]
            if any("Not Found" in msg for msg in error_messages):
                raise ProviderNoResult(self.name, query)
            raise httpx.HTTPStatusError(
                message=f"GraphQL errors: {error_messages}",
                request=response.request,
                response=response,
            )

        media_list = ((data.get("data") or {}).get("Page") or {}).get("media") or []
        if not media_list:
            raise ProviderNoResult(self.name, query)

        media = media_list[0]
        return {"data": {"Media": media}}

    def normalize(self, query: str, raw: dict[str, Any]) -> dict[str, Any]:
        media = (raw.get("data") or {}).get("Media") or {}
        if not media:
            return {}

        title = media.get("title") or {}

        characters = []
        for edge in media.get("characters", {}).get("edges", []):
            node = edge.get("node") or {}
            characters.append(
                {
                    "name": (node.get("name") or {}).get("full"),
                    "role": edge.get("role"),
                    "image": (node.get("image") or {}).get("large"),
                    "source": self.name,
                }
            )

        return {
            "titles": {
                "english": [title["english"]] if title.get("english") else [],
                "japanese": [title["native"]] if title.get("native") else [],
                "romaji": [title["romaji"]] if title.get("romaji") else [],
                "all": [title["userPreferred"]] if title.get("userPreferred") else [],
            },
            "description": {"summary": compact_html(media.get("description"))},
            "genres": media.get("genres") or [],
            "themes": [],
            "studios": [
                n.get("name")
                for n in media.get("studios", {}).get("nodes", [])
                if n.get("name")
            ],
            "characters": characters,
            "staff": [],
            "media": {
                "poster": (media.get("coverImage") or {}).get("large"),
                "banner": None,
                "trailer": None,
                "trailer_thumbnail": None,
            },
            "release": {
                "episodes": media.get("episodes"),
                "status": media.get("status"),
                "format": media.get("format"),
                "start_date": media.get("startDate"),
                "end_date": None,
            },
            "statistics": {
                "average_score": media.get("averageScore"),
                "mean_score": media.get("meanScore"),
                "popularity": media.get("popularity"),
                "trending": None,
                "rankings": [],
            },
            "relationships": [],
            "recommendations": [
                {
                    "title": ((node.get("mediaRecommendation") or {}).get("title") or {}).get("userPreferred"),
                    "url": (node.get("mediaRecommendation") or {}).get("siteUrl"),
                    "score": (node.get("mediaRecommendation") or {}).get("averageScore"),
                    "source": self.name,
                }
                for node in media.get("recommendations", {}).get("nodes", [])
                if node.get("mediaRecommendation")
            ],
            "external_links": [],
            "streaming_services": [],
        }
