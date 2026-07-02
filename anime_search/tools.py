from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import quote_plus

import httpx

log = logging.getLogger(__name__)

JIKAN_BASE = "https://api.jikan.moe/v4"
ANILIST_GRAPHQL = "https://graphql.anilist.co"
ANIMEPLANET_BASE = "https://www.anime-planet.com/api"
SHIKIMORI_BASE = "https://shikimori.one/api"


async def _jikan_get(path: str, params: dict[str, Any] | None = None, retries: int = 2) -> dict[str, Any]:
    url = f"{JIKAN_BASE}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0)) as client:
        for attempt in range(retries + 1):
            try:
                resp = await client.get(url, params=params or {})
                if resp.status_code == 429:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError:
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
            except Exception:
                if attempt < retries:
                    await asyncio.sleep(0.5)
                    continue
                raise
    return {}


async def _anilist_query(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0)) as client:
        try:
            resp = await client.post(ANILIST_GRAPHQL, json={"query": query, "variables": variables or {}})
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {}


async def _shikimori_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{SHIKIMORI_BASE}{path}"
    headers = {"User-Agent": "AnimeSearch/1.0"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0)) as client:
        try:
            resp = await client.get(url, params=params or {}, headers=headers)
            if resp.status_code == 429:
                await asyncio.sleep(1.0)
                resp = await client.get(url, params=params or {}, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {}


async def _animeplanet_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{ANIMEPLANET_BASE}{path}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0), follow_redirects=True) as client:
        try:
            resp = await client.get(url, params=params or {}, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {}


def _extract_anime_fields(anime: dict[str, Any]) -> dict[str, Any]:
    images = anime.get("images", {}).get("jpg", {}) | anime.get("images", {}).get("webp", {})
    return {
        "title": anime.get("title"),
        "title_english": anime.get("title_english"),
        "mal_id": anime.get("mal_id"),
        "score": anime.get("score"),
        "scored_by": anime.get("scored_by"),
        "rank": anime.get("rank"),
        "popularity": anime.get("popularity"),
        "episodes": anime.get("episodes"),
        "status": anime.get("status"),
        "type": anime.get("type"),
        "source": anime.get("source"),
        "rating": anime.get("rating"),
        "duration": anime.get("duration"),
        "synopsis": (anime.get("synopsis") or "")[:400],
        "background": (anime.get("background") or "")[:300],
        "genres": [g.get("name") for g in anime.get("genres", []) if g.get("name")],
        "themes": [t.get("name") for t in anime.get("themes", []) if t.get("name")],
        "studios": [s.get("name") for s in anime.get("studios", []) if s.get("name")],
        "producers": [p.get("name") for p in anime.get("producers", []) if p.get("name")],
        "poster": images.get("large_image_url") or images.get("image_url"),
        "url": anime.get("url"),
        "year": anime.get("year"),
        "season": anime.get("season"),
        "members": anime.get("members"),
        "favorites": anime.get("favorites"),
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_anime_by_title",
            "description": "Search for anime by title across multiple APIs. Returns detailed info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Anime title to search for"},
                    "limit": {"type": "integer", "description": "Max results (default 5)"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_anime_by_genre",
            "description": "Search anime by genre. Returns top scored anime in that genre.",
            "parameters": {
                "type": "object",
                "properties": {
                    "genre": {"type": "string", "description": "Genre name (Action, Romance, Comedy, Drama, Fantasy, Horror, Mystery, Sci-Fi, Slice of Life, Sports, Supernatural, Thriller, Mecha)"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["genre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_anime_by_studio",
            "description": "Search anime by studio name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "studio": {"type": "string", "description": "Studio name (Madhouse, Kyoto Animation, ufotable, MAPPA, Bones, Wit Studio)"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["studio"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_anime_by_theme",
            "description": "Search anime by theme (Isekai, School, Military, Music, Vampire, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string", "description": "Theme name"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["theme"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_anime_by_keyword",
            "description": "Search anime by any keyword (vampire, tournament, cooking, post-apocalyptic, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Keyword to search for"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_anime_recommendations",
            "description": "Get anime recommendations based on a title. Returns similar anime from MAL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Anime title to get recommendations for"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_rated_anime",
            "description": "Get top rated anime from MAL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "enum": ["airing", "upcoming", "bypopularity", "favorite"], "description": "Filter type"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_seasonal_anime",
            "description": "Get current or upcoming seasonal anime.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "Year (e.g. 2024)"},
                    "season": {"type": "string", "enum": ["winter", "spring", "summer", "fall"], "description": "Season"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_anime_schedule",
            "description": "Get anime airing schedule by day of week.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "enum": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"], "description": "Day of week"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_anime_details",
            "description": "Get full details for a specific anime including synopsis, genres, themes, studios, characters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Anime title to get details for"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_anime",
            "description": "Compare two anime side by side.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_a": {"type": "string", "description": "First anime title"},
                    "title_b": {"type": "string", "description": "Second anime title"},
                },
                "required": ["title_a", "title_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_similar_by_character",
            "description": "Find anime with similar character traits (hair color, personality, abilities).",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "Character name to find similar to"},
                    "trait": {"type": "string", "description": "Trait to match (hair color, personality, ability)"},
                },
                "required": ["character_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_anime_multi_api",
            "description": "Search anime across ALL APIs (Jikan, AniList, Shikimori) for maximum coverage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results per API (default 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_by_description_keywords",
            "description": "Parse a natural language description and search for matching anime. Extracts genre, tone, setting, themes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Natural language description of desired anime"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_anime_by_demographic",
            "description": "Search by demographic (Shounen, Shoujo, Seinen, Josei).",
            "parameters": {
                "type": "object",
                "properties": {
                    "demographic": {"type": "string", "enum": ["shounen", "shoujo", "seinen", "josei"], "description": "Target demographic"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["demographic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_anime_by_year",
            "description": "Get top anime from a specific year.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer", "description": "Year (1990-2026)"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["year"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_anime_by_source",
            "description": "Search by source material (Manga, Light Novel, Visual Novel, Original, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Source material (Manga, Light Novel, Visual Novel, Original, etc.)"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_anime",
            "description": "Search the web for anime information, reviews, and recommendations using DuckDuckGo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query for anime info"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_wikipedia",
            "description": "Search Wikipedia for anime articles and summaries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Anime title or topic to search on Wikipedia"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_fandom",
            "description": "Search Fandom/Wikia for anime wiki articles and detailed info.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Anime title or topic to search on Fandom"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch_url",
            "description": "Fetch content from a URL. Use this to read anime pages, reviews, or articles.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch content from"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "Semantic search that parses description and finds matching anime by genre, theme, and keywords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query or description"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hybrid_recommend",
            "description": "Hybrid recommendation combining keyword search and description analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query or description"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["query"],
            },
        },
    },
]


TOOL_MAP = {t["function"]["name"]: t for t in TOOL_DEFINITIONS}


async def search_anime_by_title(title: str, limit: int = 5) -> list[dict[str, Any]]:
    data = await _jikan_get("/anime", {"q": title, "limit": limit, "sfw": "true"})
    return [_extract_anime_fields(a) for a in data.get("data", [])[:limit]]


async def search_anime_by_genre(genre: str, limit: int = 10) -> list[dict[str, Any]]:
    genre_map = {
        "action": 1, "adventure": 2, "comedy": 4, "drama": 8, "ecchi": 9,
        "fantasy": 10, "horror": 14, "mystery": 7, "romance": 22, "sci-fi": 24,
        "slice of life": 36, "sports": 30, "supernatural": 37, "thriller": 41,
        "mecha": 18, "isekai": 62, "school": 22, "military": 38,
    }
    genre_id = genre_map.get(genre.lower())
    if not genre_id:
        data = await _jikan_get("/genres/anime")
        for g in data.get("data", []):
            if g.get("name", "").lower() == genre.lower():
                genre_id = g.get("mal_id")
                break
    if not genre_id:
        return []
    data = await _jikan_get("/anime", {"genres": genre_id, "limit": limit, "sfw": "true", "order_by": "score", "sort": "desc"})
    return [_extract_anime_fields(a) for a in data.get("data", [])[:limit]]


async def search_anime_by_studio(studio: str, limit: int = 10) -> list[dict[str, Any]]:
    producers_data = await _jikan_get("/producers", {"q": studio, "limit": 1})
    producer = (producers_data.get("data") or [{}])[0]
    mal_id = producer.get("mal_id")
    if not mal_id:
        return []
    data = await _jikan_get(f"/producers/{mal_id}/full", {"page": 1})
    results = []
    for item in data.get("data", [])[:limit]:
        results.extend(_extract_anime_fields(a) for a in [item] if item.get("mal_id"))
    return results


async def search_anime_by_theme(theme: str, limit: int = 10) -> list[dict[str, Any]]:
    data = await _jikan_get("/anime", {"q": theme, "limit": limit, "sfw": "true", "order_by": "score", "sort": "desc"})
    return [_extract_anime_fields(a) for a in data.get("data", [])[:limit]]


async def search_anime_by_keyword(keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    data = await _jikan_get("/anime", {"q": keyword, "limit": limit, "sfw": "true", "order_by": "score", "sort": "desc"})
    return [_extract_anime_fields(a) for a in data.get("data", [])[:limit]]


async def get_anime_recommendations(title: str, limit: int = 10) -> list[dict[str, Any]]:
    search_data = await _jikan_get("/anime", {"q": title, "limit": 1, "sfw": "true"})
    anime = (search_data.get("data") or [{}])[0]
    mal_id = anime.get("mal_id")
    if not mal_id:
        return []
    rec_data = await _jikan_get(f"/anime/{mal_id}/recommendations")
    return [
        {"title": item.get("entry", {}).get("title"), "url": item.get("entry", {}).get("url")}
        for item in rec_data.get("data", [])[:limit]
    ]


async def get_top_rated_anime(filter: str = "bypopularity", limit: int = 10) -> list[dict[str, Any]]:
    data = await _jikan_get("/top/anime", {"filter": filter, "limit": limit})
    return [_extract_anime_fields(a) for a in data.get("data", [])[:limit]]


async def get_seasonal_anime(year: int | None = None, season: str | None = None) -> list[dict[str, Any]]:
    import datetime
    if not year or not season:
        now = datetime.datetime.now()
        year = now.year
        month = now.month
        season = "winter" if month <= 3 else "spring" if month <= 6 else "summer" if month <= 9 else "fall"
    data = await _jikan_get(f"/seasons/{year}/{season}", {"limit": 25, "sfw": "true"})
    return [_extract_anime_fields(a) for a in data.get("data", [])]


async def search_anime_schedule(filter: str = "monday") -> list[dict[str, Any]]:
    data = await _jikan_get("/schedules", {"filter": filter, "limit": 25, "sfw": "true"})
    return [_extract_anime_fields(a) for a in data.get("data", [])]


async def get_anime_details(title: str) -> dict[str, Any]:
    search_data = await _jikan_get("/anime", {"q": title, "limit": 1, "sfw": "true"})
    anime = (search_data.get("data") or [{}])[0]
    if not anime:
        return {"error": "Anime not found"}
    mal_id = anime.get("mal_id")
    if not mal_id:
        return {"error": "No MAL ID found"}
    detail_data = await _jikan_get(f"/anime/{mal_id}/full")
    full = detail_data.get("data", anime)
    images = full.get("images", {}).get("jpg", {}) | full.get("images", {}).get("webp", {})
    return {
        "title": full.get("title"),
        "title_english": full.get("title_english"),
        "title_japanese": full.get("title_japanese"),
        "score": full.get("score"),
        "scored_by": full.get("scored_by"),
        "rank": full.get("rank"),
        "popularity": full.get("popularity"),
        "episodes": full.get("episodes"),
        "status": full.get("status"),
        "type": full.get("type"),
        "source": full.get("source"),
        "duration": full.get("duration"),
        "rating": full.get("rating"),
        "synopsis": full.get("synopsis"),
        "background": full.get("background"),
        "genres": [g.get("name") for g in full.get("genres", [])],
        "themes": [t.get("name") for t in full.get("themes", [])],
        "studios": [s.get("name") for s in full.get("studios", [])],
        "producers": [p.get("name") for p in full.get("producers", [])],
        "poster": images.get("large_image_url") or images.get("image_url"),
        "url": full.get("url"),
        "aired": full.get("aired", {}).get("string"),
        "trailer": full.get("trailer", {}).get("url"),
        "members": full.get("members"),
        "favorites": full.get("favorites"),
    }


async def compare_anime(title_a: str, title_b: str) -> dict[str, Any]:
    results = await asyncio.gather(
        search_anime_by_title(title_a, limit=1),
        search_anime_by_title(title_b, limit=1),
        return_exceptions=True,
    )
    a = results[0][0] if isinstance(results[0], list) and results[0] else {}
    b = results[1][0] if isinstance(results[1], list) and results[1] else {}
    return {"anime_a": a, "anime_b": b}


async def find_similar_by_character(character_name: str, trait: str = "") -> list[dict[str, Any]]:
    data = await _jikan_get("/characters", {"q": character_name, "limit": 5})
    results = []
    for char in data.get("data", [])[:3]:
        mal_id = char.get("mal_id")
        if mal_id:
            anime_data = await _jikan_get(f"/characters/{mal_id}/full")
            for item in anime_data.get("data", {}).get("anime", [])[:3]:
                anime = item.get("anime", {})
                results.extend(_extract_anime_fields(a) for a in [anime] if anime.get("mal_id"))
    return results[:10]


async def search_anime_multi_api(query: str, limit: int = 5) -> list[dict[str, Any]]:
    jikan_task = search_anime_by_title(query, limit)
    anilist_query = """
    query ($search: String, $limit: Int) {
        Page(page: 1, perPage: $limit) {
            media(search: $search, type: ANIME) {
                id
                title { romaji english }
                description
                averageScore
                genres
                studios(isMain: true) { nodes { name } }
                coverImage { large }
                meanScore
                popularity
                episodes
                status
                source
            }
        }
    }
    """
    anilist_task = _anilist_query(anilist_query, {"search": query, "limit": limit})
    shikimori_task = _shikimori_get("/animes", {"search": query, "limit": limit})

    jikan_results, anilist_result, shikimori_results = await asyncio.gather(
        jikan_task, anilist_task, shikimori_task, return_exceptions=True
    )

    all_results = []

    if isinstance(jikan_results, list):
        all_results.extend(jikan_results)

    if isinstance(anilist_result, dict):
        for media in anilist_result.get("data", {}).get("Page", {}).get("media", []):
            title_obj = media.get("title", {})
            all_results.append({
                "title": title_obj.get("romaji") or title_obj.get("english", ""),
                "title_english": title_obj.get("english"),
                "score": media.get("averageScore"),
                "synopsis": (media.get("description") or "")[:400],
                "genres": media.get("genres", []),
                "studios": [n.get("name") for n in media.get("studios", {}).get("nodes", [])],
                "poster": media.get("coverImage", {}).get("large"),
                "episodes": media.get("episodes"),
                "status": media.get("status"),
                "source": "anilist",
            })

    if isinstance(shikimori_results, list):
        for item in shikimori_results:
            all_results.append({
                "title": item.get("name") or item.get("russian", ""),
                "title_english": item.get("english"),
                "score": item.get("score"),
                "synopsis": (item.get("description") or "")[:400],
                "genres": [g.get("name") for g in item.get("genres", [])],
                "poster": item.get("image", {}).get("original"),
                "episodes": item.get("episodes"),
                "status": item.get("status"),
                "source": "shikimori",
            })

    seen = set()
    unique = []
    for r in all_results:
        key = (r.get("title") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    return unique[:limit * 3]


DESCRIPTION_KEYWORD_MAP = {
    "romance": ["romance", "romantic", "love", "dating", "relationship", "couple", " crush"],
    "action": ["action", "battle", "fight", "combat", "war", "battle", "action-packed"],
    "comedy": ["comedy", "funny", "humor", "hilarious", "laugh", "slapstick", "parody"],
    "drama": ["drama", "emotional", "sad", "tearjerker", "heartfelt", "serious"],
    "fantasy": ["fantasy", "magic", "magical", "wizard", "dragon", "medieval", "sword"],
    "sci-fi": ["sci-fi", "science fiction", "space", "future", "cyberpunk", "technology", "robot"],
    "horror": ["horror", "scary", "creepy", "恐怖", "gore", "psychological horror"],
    "slice of life": ["slice of life", "daily life", "school life", "comforting", "relaxing", "cute"],
    "sports": ["sports", "tournament", "competition", "team", "athlete", "training"],
    "isekai": ["isekai", "another world", "transported", "reincarnated", "summoned", "fantasy world"],
    "supernatural": ["supernatural", "ghost", "demon", "vampire", "monster", "paranormal"],
    "thriller": ["thriller", "suspense", "mystery", "tension", "mind games", "psychological"],
    "mecha": ["mecha", "robot", "pilot", "giant robot", "mech"],
    "military": ["military", "army", "soldier", "war", "tactical", "strategy"],
    "music": ["music", "band", "idol", "concert", "singing", "performance"],
    "harem": ["harem", "multiple love interests", "romantic rivals"],
    "ecchi": ["ecchi", "fan service", "sexy", "nsfw", "suggestive"],
    "school": ["school", "classroom", "student", "teacher", "academy", "high school"],
    "historical": ["historical", "period", "ancient", "samurai", "feudal", "victorian"],
    "vampire": ["vampire", "blood", "immortal", "dark"],
    "post-apocalyptic": ["post-apocalyptic", "apocalypse", "dystopia", "end of world", "ruins"],
    "psychological": ["psychological", "mind games", "mental", "sanity", "twisted"],
    "mystery": ["mystery", "detective", "investigation", "crime", "whodunit"],
    "adventure": ["adventure", "journey", "quest", "exploration", "travel"],
    "martial arts": ["martial arts", "karate", "kung fu", "fighting style", "tournament"],
}


def parse_description(description: str) -> dict[str, Any]:
    desc_lower = description.lower()
    matched_genres = []
    matched_themes = []
    matched_tones = []
    matched_settings = []
    matched_character_types = []

    for genre, keywords in DESCRIPTION_KEYWORD_MAP.items():
        for kw in keywords:
            if kw in desc_lower:
                matched_genres.append(genre)
                break

    tone_keywords = {
        "dark": "dark", "gritty": "dark", "serious": "dark", "bleak": "dark",
        "lighthearted": "lighthearted", "fun": "lighthearted", "cute": "lighthearted", "wholesome": "lighthearted",
        "intense": "intense", "fast-paced": "intense", "action-packed": "intense",
        "emotional": "emotional", "touching": "emotional", "heartwarming": "emotional",
        "mysterious": "mysterious", "enigmatic": "mysterious",
        "dark humor": "dark humor", "satirical": "dark humor",
    }
    for kw, tone in tone_keywords.items():
        if kw in desc_lower:
            matched_tones.append(tone)

    setting_keywords = {
        "school": "school", "academy": "school", "classroom": "school", "high school": "school",
        "fantasy world": "fantasy", "medieval": "fantasy", "magical kingdom": "fantasy",
        "space": "space", "spaceship": "space", "galaxy": "space",
        "future": "futuristic", "cyberpunk": "futuristic", "dystopia": "futuristic",
        "japan": "japan", "tokyo": "japan",
        "military": "military", "army": "military",
        "city": "urban", "urban": "urban",
    }
    for kw, setting in setting_keywords.items():
        if kw in desc_lower:
            matched_settings.append(setting)

    char_keywords = {
        "strong female": "strong female lead",
        "female lead": "female lead",
        "male lead": "male lead",
        "overpowered": "overpowered protagonist",
        "weak protagonist": "underdog protagonist",
        "genius": "genius protagonist",
        "anti-hero": "anti-hero",
        "team": "team dynamics",
        "rival": "rivalry",
        "mentor": "mentor figure",
        "orphan": "orphan protagonist",
    }
    for kw, char_type in char_keywords.items():
        if kw in desc_lower:
            matched_character_types.append(char_type)

    return {
        "genres": list(set(matched_genres)),
        "tones": list(set(matched_tones)),
        "settings": list(set(matched_settings)),
        "character_types": list(set(matched_character_types)),
        "original_description": description,
    }


async def search_by_description_keywords(description: str, limit: int = 10) -> list[dict[str, Any]]:
    parsed = parse_description(description)
    all_results = []

    search_queries = []
    if parsed["genres"]:
        search_queries.extend(parsed["genres"][:3])
    if parsed["settings"]:
        search_queries.extend(parsed["settings"][:2])
    if parsed["character_types"]:
        search_queries.extend(parsed["character_types"][:2])
    if not search_queries:
        words = description.split()[:5]
        search_queries.append(" ".join(words))

    for query in search_queries[:4]:
        results = await search_anime_by_title(query, limit=limit)
        all_results.extend(results)
        await asyncio.sleep(0.3)

    if parsed["genres"]:
        for genre in parsed["genres"][:2]:
            results = await search_anime_by_genre(genre, limit=limit)
            all_results.extend(results)
            await asyncio.sleep(0.3)

    seen = set()
    unique = []
    for r in all_results:
        key = (r.get("title") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    return unique[:limit]


async def get_anime_by_demographic(demographic: str, limit: int = 10) -> list[dict[str, Any]]:
    demo_map = {"shounen": 27, "shoujo": 25, "seinen": 42, "josei": 43}
    demo_id = demo_map.get(demographic.lower())
    if not demo_id:
        return []
    data = await _jikan_get("/anime", {"demographics": demo_id, "limit": limit, "sfw": "true", "order_by": "score", "sort": "desc"})
    return [_extract_anime_fields(a) for a in data.get("data", [])[:limit]]


async def get_anime_by_year(year: int, limit: int = 10) -> list[dict[str, Any]]:
    data = await _jikan_get("/anime", {"start_date": f"{year}-01-01", "end_date": f"{year}-12-31", "limit": limit, "sfw": "true", "order_by": "score", "sort": "desc"})
    return [_extract_anime_fields(a) for a in data.get("data", [])[:limit]]


async def get_anime_by_source(source: str, limit: int = 10) -> list[dict[str, Any]]:
    data = await _jikan_get("/anime", {"q": source, "limit": limit, "sfw": "true", "order_by": "score", "sort": "desc"})
    return [_extract_anime_fields(a) for a in data.get("data", [])[:limit]]


TOOL_EXECUTORS: dict[str, Any] = {
    "search_anime_by_title": search_anime_by_title,
    "search_anime_by_genre": search_anime_by_genre,
    "search_anime_by_studio": search_anime_by_studio,
    "search_anime_by_theme": search_anime_by_theme,
    "search_anime_by_keyword": search_anime_by_keyword,
    "get_anime_recommendations": get_anime_recommendations,
    "get_top_rated_anime": get_top_rated_anime,
    "get_seasonal_anime": get_seasonal_anime,
    "search_anime_schedule": search_anime_schedule,
    "get_anime_details": get_anime_details,
    "compare_anime": compare_anime,
    "find_similar_by_character": find_similar_by_character,
    "search_anime_multi_api": search_anime_multi_api,
    "search_by_description_keywords": search_by_description_keywords,
    "get_anime_by_demographic": get_anime_by_demographic,
    "get_anime_by_year": get_anime_by_year,
    "get_anime_by_source": get_anime_by_source,
}


async def web_search_anime(query: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0), follow_redirects=True) as client:
            resp = await client.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": f"anime {query}"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            resp.raise_for_status()
            text = resp.text
            results = []
            import re
            import html
            links = re.findall(r'href=[\'"]([^\'"]+)[\'"][^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>', text, re.IGNORECASE)
            snippets = re.findall(r'<td[^>]*class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', text, re.IGNORECASE | re.DOTALL)
            for i in range(min(10, len(links))):
                url = links[i][0]
                title = re.sub(r'<[^>]+>', '', links[i][1]).strip()
                title = html.unescape(title)
                snippet = ""
                if i < len(snippets):
                    snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
                    snippet = html.unescape(snippet)
                if title and url:
                    results.append({"title": title, "snippet": snippet, "url": url})
            return {"results": results}
    except Exception as exc:
        return {"error": str(exc)}


async def web_search_wikipedia(query: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0), follow_redirects=True) as client:
            search_resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": f"anime {query}", "format": "json", "srlimit": 5},
            )
            search_resp.raise_for_status()
            search_data = search_resp.json()
            results = []
            for item in search_data.get("query", {}).get("search", []):
                title = item.get("title", "")
                snippet = re.sub(r'<[^>]+>', '', item.get("snippet", "")).strip()
                page_resp = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={"action": "query", "titles": title, "prop": "extracts", "exintro": True, "explaintext": True, "format": "json", "exchars": 500},
                )
                page_data = page_resp.json()
                pages = page_data.get("query", {}).get("pages", {})
                extract = ""
                for page in pages.values():
                    extract = page.get("extract", "")[:500]
                results.append({
                    "title": title,
                    "snippet": snippet,
                    "extract": extract,
                    "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                })
            return {"results": results[:5]}
    except Exception as exc:
        return {"error": str(exc)}


async def web_search_fandom(query: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0), follow_redirects=True) as client:
            resp = await client.get(
                "https://www.fandom.com/search",
                params={"q": f"anime {query}", "type": "articles"},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            resp.raise_for_status()
            text = resp.text
            results = []
            import re
            for match in re.finditer(r'class="result-title"[^>]*>(.*?)</a>.*?class="result-snippet"[^>]*>(.*?)</p>', text, re.DOTALL):
                title, snippet = match.groups()
                title = re.sub(r'<[^>]+>', '', title).strip()
                snippet = re.sub(r'<[^>]+>', '', snippet).strip()
                if title:
                    results.append({"title": title, "snippet": snippet, "url": f"https://www.fandom.com/search?q={query}"})
            if not results:
                wiki_resp = await client.get(
                    "https://anime.fandom.com/api/v1/Search/List",
                    params={"query": query, "limit": 5},
                )
                if wiki_resp.status_code == 200:
                    for item in wiki_resp.json().get("items", []):
                        results.append({
                            "title": item.get("title", ""),
                            "snippet": item.get("snippet", ""),
                            "url": item.get("url", ""),
                        })
            return {"results": results[:5]}
    except Exception as exc:
        return {"error": str(exc)}


async def web_fetch_url(url: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0), follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            text = resp.text[:5000]
            return {"content": text}
    except Exception as exc:
        return {"error": str(exc)}


TOOL_EXECUTORS["web_search_anime"] = web_search_anime
TOOL_EXECUTORS["web_fetch_url"] = web_fetch_url
TOOL_EXECUTORS["web_search_wikipedia"] = web_search_wikipedia
TOOL_EXECUTORS["web_search_fandom"] = web_search_fandom


async def semantic_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    parsed = parse_description(query)
    all_results = []
    if parsed["genres"]:
        for genre in parsed["genres"][:2]:
            results = await search_anime_by_genre(genre, limit=limit)
            all_results.extend(results)
            await asyncio.sleep(0.3)
    words = query.split()[:5]
    for word in words:
        if len(word) > 3:
            results = await search_anime_by_keyword(word, limit=5)
            all_results.extend(results)
            await asyncio.sleep(0.2)
    seen = set()
    unique = []
    for r in all_results:
        key = (r.get("title") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:limit]


async def hybrid_recommend(query: str, limit: int = 10) -> list[dict[str, Any]]:
    keyword_results = await search_anime_by_keyword(query, limit)
    desc_results = await search_by_description_keywords(query, limit)
    all_results = keyword_results + desc_results
    seen = set()
    unique = []
    for r in all_results:
        key = (r.get("title") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    return unique[:limit]


TOOL_EXECUTORS["semantic_search"] = semantic_search
TOOL_EXECUTORS["hybrid_recommend"] = hybrid_recommend


async def execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    executor = TOOL_EXECUTORS.get(name)
    if not executor:
        return {"error": f"Unknown tool: {name}"}
    try:
        result = await executor(**arguments)
        return {"result": result}
    except Exception as e:
        log.warning("Tool %s failed: %s", name, e)
        return {"error": str(e)}
