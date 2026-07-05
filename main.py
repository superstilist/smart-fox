from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx
from flask import Flask, Response, jsonify, render_template, request

from anime_search.config import (
    auto_init_session,
    load_settings,
    save_settings,
    list_sessions,
    save_session,
    load_session,
    delete_session,
    rename_session,
    get_active_session_name,
    set_active_session_name,
    load_token_usage,
    record_token_usage,
    get_session_token_usage,
)
from anime_search.engine import AnimeSearchEngine, _get_task, cancel_task, cleanup_old_tasks
from anime_search.library import (
    get_library,
    get_sorted_library,
    add_entry,
    add_or_update_entry,
    update_entry,
    remove_entry,
    get_entry,
    search_library,
    bulk_remove,
    bulk_update_status,
    export_library,
    import_library,
)

log = logging.getLogger(__name__)

POSTER_CACHE: dict[str, dict[str, Any]] = {}
POSTER_CACHE_TTL = 86400


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _record_if_usage(recommendation: dict[str, Any] | None) -> None:
    if not recommendation:
        return
    usage = recommendation.get("token_usage")
    if usage:
        active = get_active_session_name()
        if active:
            record_token_usage(active, usage)


async def fetch_anilist_poster(
    client: httpx.AsyncClient,
    title: str,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Fetch poster and info from AniList GraphQL API."""
    query = """
    query ($search: String) {
      Media(search: $search, type: ANIME) {
        id
        title { romaji english native }
        description(asHtml: false)
        coverImage { large medium }
        format
        status
        episodes
        genres
        averageScore
        meanScore
      }
    }
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "SmartFox/1.0 (anime-search-app)",
    }
    try:
        response = await client.post(
            "https://graphql.anilist.co",
            json={"query": query, "variables": {"search": title}},
            headers=headers,
            timeout=timeout,
        )
        if response.status_code != 200:
            log.debug("AniList returned %d for '%s'", response.status_code, title)
            return None
        data = response.json()
        media = data.get("data", {}).get("Media")
        if not media:
            return None

        poster_url = media.get("coverImage", {}).get("large") or media.get("coverImage", {}).get("medium") or ""
        synopsis = (media.get("description") or "").replace("<br>", "\n").replace("</br>", "").replace("<br/>", "")
        # Strip HTML tags
        import re
        synopsis = re.sub(r"<[^>]+>", "", synopsis).strip()[:300]

        return {
            "poster": poster_url,
            "score": media.get("averageScore") or media.get("meanScore"),
            "episodes": media.get("episodes"),
            "synopsis": synopsis,
            "genres": (media.get("genres") or [])[:6],
            "type": media.get("format"),
            "source": "AniList",
        }
    except Exception as e:
        log.debug("AniList poster fetch failed for '%s': %s", title, e)
        return None


async def fetch_jikan_poster(
    client: httpx.AsyncClient,
    title: str,
    content_filter: str = "sfw",
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    """Fetch poster from Jikan (MAL) API as fallback."""
    try:
        params = {"q": title, "limit": 1}
        if content_filter == "sfw":
            params["sfw"] = "true"
        elif content_filter == "nsfw":
            params["rating"] = "rx"
        response = await client.get(
            "https://api.jikan.moe/v4/anime",
            params=params,
            timeout=timeout,
        )
        if response.status_code == 429:
            await asyncio.sleep(1.0)
            response = await client.get(
                "https://api.jikan.moe/v4/anime",
                params=params,
                timeout=timeout,
            )
        response.raise_for_status()
        data = response.json()
        anime = (data.get("data") or [{}])[0]
        images = anime.get("images", {}).get("jpg", {}) | anime.get("images", {}).get("webp", {})
        raw_poster = (images.get("large_image_url") or images.get("image_url") or "").replace("http://", "https://")
        return {
            "poster": raw_poster,
            "score": anime.get("score"),
            "episodes": anime.get("episodes"),
            "synopsis": (anime.get("synopsis") or "")[:300],
            "genres": [g.get("name") for g in anime.get("genres", []) if g.get("name")][:6],
            "type": anime.get("type"),
            "source": "Jikan",
        }
    except Exception as e:
        log.debug("Jikan poster fetch failed for '%s': %s", title, e)
        return None


async def fetch_kitsu_poster(
    client: httpx.AsyncClient,
    title: str,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Fetch poster from Kitsu API as second fallback."""
    try:
        headers = {"Accept": "application/vnd.api+json"}
        params = {"filter[text]": title, "page[limit]": 1}
        response = await client.get(
            "https://kitsu.io/api/edge/anime",
            params=params,
            headers=headers,
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        anime_list = data.get("data") or []
        anime = anime_list[0] if anime_list else {}
        attrs = anime.get("attributes") or {}
        poster = attrs.get("posterImage") or {}
        return {
            "poster": poster.get("original") or poster.get("large") or "",
            "score": float(attrs.get("averageRating") or 0) if attrs.get("averageRating") else None,
            "episodes": attrs.get("episodeCount"),
            "synopsis": (attrs.get("synopsis") or "")[:300],
            "genres": [],
            "type": attrs.get("showType"),
            "source": "Kitsu",
        }
    except Exception as e:
        log.debug("Kitsu poster fetch failed for '%s': %s", title, e)
        return None


async def fetch_poster_batch(
    titles: list[str],
    content_filter: str = "sfw",
    timeout: float = 8.0,
    delay: float = 0.3,
) -> dict[str, dict[str, Any]]:
    """Fetch posters using AniList (primary), Jikan, then Kitsu fallback."""
    now = time.time()
    results: dict[str, dict[str, Any]] = {}
    to_fetch: list[str] = []
    for title in titles:
        cached = POSTER_CACHE.get(title)
        if cached and now - cached.get("_ts", 0) < POSTER_CACHE_TTL:
            results[title] = {k: v for k, v in cached.items() if k != "_ts"}
        else:
            to_fetch.append(title)

    if not to_fetch:
        return results

    semaphore = asyncio.Semaphore(5)

    async def _fetch_one(client: httpx.AsyncClient, title: str) -> None:
        async with semaphore:
            poster_info = None
            # Try AniList first
            poster_info = await fetch_anilist_poster(client, title, timeout=5.0)
            # Fallback to Jikan if AniList fails
            if not poster_info or not poster_info.get("poster"):
                await asyncio.sleep(0.5)  # Rate limit for Jikan
                poster_info = await fetch_jikan_poster(client, title, content_filter, timeout=6.0)
            # Fallback to Kitsu if Jikan fails
            if not poster_info or not poster_info.get("poster"):
                await asyncio.sleep(0.3)
                poster_info = await fetch_kitsu_poster(client, title, timeout=5.0)
            # Final fallback
            if not poster_info:
                poster_info = {"poster": None}

            results[title] = poster_info
            POSTER_CACHE[title] = {**poster_info, "_ts": time.time()}
            await asyncio.sleep(delay)

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
        tasks = [_fetch_one(client, t) for t in to_fetch[:50]]
        await asyncio.gather(*tasks, return_exceptions=True)
    return results


async def fetch_anime_detail(
    title: str,
    content_filter: str = "sfw",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch full anime details using AniList (primary), Jikan, then Kitsu fallback."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
            # Try AniList first
            anilist_query = """
            query ($search: String) {
              Media(search: $search, type: ANIME) {
                id
                title { romaji english native }
                description(asHtml: false)
                coverImage { large medium }
                bannerImage
                format
                status
                episodes
                duration
                genres
                tags { name }
                averageScore
                meanScore
                popularity
                favourites
                nextAiringEpisode { episode airingAt }
                mediaMal { id }
                ExternalLinks { site url }
                streamingEpisodes { title thumbnail url site }
                relations { edges { relationType node { id title { romaji } type } } }
                characters(sort: ROLE, perPage: 12) { edges { role node { id name { full } image { medium } } } }
                studios(isMain: true) { nodes { name } }
                producers { nodes { name } }
              }
            }
            """
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "SmartFox/1.0 (anime-search-app)",
                }
                response = await client.post(
                    "https://graphql.anilist.co",
                    json={"query": anilist_query, "variables": {"search": title}},
                    headers=headers,
                    timeout=6.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    media = data.get("data", {}).get("Media")
                    if media:
                        synopsis = (media.get("description") or "").replace("<br>", "\n").replace("</br>", "").replace("<br/>", "")
                        import re
                        synopsis = re.sub(r"<[^>]+>", "", synopsis).strip()

                        genres = media.get("genres") or []
                        themes_list = [t.get("name") for t in (media.get("tags") or []) if t.get("name")][:10]

                        result = {
                            "title": media.get("title", {}).get("romaji") or title,
                            "title_english": media.get("title", {}).get("english") or "",
                            "title_japanese": media.get("title", {}).get("native") or "",
                            "poster": media.get("coverImage", {}).get("large") or media.get("coverImage", {}).get("medium") or "",
                            "banner": media.get("bannerImage") or "",
                            "synopsis": synopsis,
                            "genres": genres,
                            "themes": themes_list,
                            "score": media.get("averageScore") or media.get("meanScore"),
                            "episodes": media.get("episodes"),
                            "duration": media.get("duration"),
                            "status": media.get("status"),
                            "type": media.get("format"),
                            "source": "AniList",
                            "mal_id": media.get("mediaMal", {}).get("id"),
                            "popularity": media.get("popularity"),
                            "favorites": media.get("favourites"),
                            "url": f"https://anilist.co/anime/{media.get('id')}",
                            "characters": [
                                {
                                    "name": e.get("node", {}).get("name", {}).get("full", ""),
                                    "image": e.get("node", {}).get("image", {}).get("medium", ""),
                                    "role": e.get("role", ""),
                                }
                                for e in (media.get("characters", {}).get("edges") or [])[:12]
                            ],
                            "studios": [n.get("name") for n in (media.get("studios", {}).get("nodes") or [])],
                            "producers": [n.get("name") for n in (media.get("producers", {}).get("nodes") or [])],
                            "external_links": [
                                {"site": l.get("site", ""), "url": l.get("url", "")}
                                for l in (media.get("ExternalLinks") or []) if l.get("url")
                            ],
                            "streaming": [
                                {"title": s.get("title", ""), "url": s.get("url", ""), "site": s.get("site", "")}
                                for s in (media.get("streamingEpisodes") or []) if s.get("url")
                            ],
                            "relations": [
                                {"relation": e.get("relationType", ""), "name": e.get("node", {}).get("title", {}).get("romaji", ""), "type": e.get("node", {}).get("type", "")}
                                for e in (media.get("relations", {}).get("edges") or [])
                            ],
                        }
                        log.info("AniList detail OK for '%s'", title)
                        return result
            except Exception as e:
                log.debug("AniList detail failed for '%s': %s", title, e)

            # Fallback to Jikan
            try:
                log.info("Falling back to Jikan for '%s'", title)
                params = {"q": title, "limit": 1}
                if content_filter == "sfw":
                    params["sfw"] = "true"
                elif content_filter == "nsfw":
                    params["rating"] = "rx"
                response = await client.get(
                    "https://api.jikan.moe/v4/anime",
                    params=params,
                    timeout=6.0,
                )
                if response.status_code == 429:
                    await asyncio.sleep(1.0)
                    response = await client.get(
                        "https://api.jikan.moe/v4/anime",
                        params=params,
                        timeout=6.0,
                    )
                response.raise_for_status()
                data = response.json()
                anime = (data.get("data") or [{}])[0]
                if anime:
                    mal_id = anime.get("mal_id")
                    images = anime.get("images", {}).get("jpg", {}) | anime.get("images", {}).get("webp", {})
                    raw_poster = (images.get("large_image_url") or images.get("image_url") or "").replace("http://", "https://")
                    raw_banner = (anime.get("images", {}).get("jpg", {}).get("large_image_url") or "").replace("http://", "https://")
                    genres = [g.get("name") for g in anime.get("genres", []) if g.get("name")]
                    themes_list = [t.get("name") for t in anime.get("themes", []) if t.get("name")]
                    demographics = [d.get("name") for d in anime.get("demographics", []) if d.get("name")]

                    result = {
                        "title": anime.get("title") or title,
                        "title_english": anime.get("title_english") or "",
                        "title_japanese": anime.get("title_japanese") or "",
                        "poster": raw_poster,
                        "banner": raw_banner,
                        "synopsis": (anime.get("synopsis") or "")[:500],
                        "genres": genres,
                        "themes": themes_list + demographics,
                        "score": anime.get("score"),
                        "episodes": anime.get("episodes"),
                        "duration": anime.get("duration"),
                        "status": anime.get("status"),
                        "type": anime.get("type"),
                        "source": "Jikan",
                        "mal_id": mal_id,
                        "url": anime.get("url") or (f"https://myanimelist.net/anime/{mal_id}" if mal_id else ""),
                        "characters": [],
                        "studios": [s.get("name") for s in anime.get("studios", []) if s.get("name")],
                        "producers": [p.get("name") for p in anime.get("producers", []) if p.get("name")],
                        "external_links": [],
                        "streaming": [],
                        "relations": [],
                    }
                    log.info("Jikan detail OK for '%s'", title)
                    return result
            except Exception as e:
                log.debug("Jikan detail failed for '%s': %s", title, e)

            # Fallback to Kitsu
            try:
                log.info("Falling back to Kitsu for '%s'", title)
                kitsu_headers = {"Accept": "application/vnd.api+json"}
                kitsu_params = {"filter[text]": title, "page[limit]": 1}
                response = await client.get(
                    "https://kitsu.io/api/edge/anime",
                    params=kitsu_params,
                    headers=kitsu_headers,
                    timeout=6.0,
                )
                response.raise_for_status()
                data = response.json()
                anime_list = data.get("data") or []
                anime = anime_list[0] if anime_list else {}
                attrs = anime.get("attributes") or {}
                titles = attrs.get("titles") or {}
                poster = attrs.get("posterImage") or {}
                cover = attrs.get("coverImage") or {}

                synopsis = (attrs.get("synopsis") or "")[:500]

                result = {
                    "title": attrs.get("canonicalTitle") or title,
                    "title_english": titles.get("en") or "",
                    "title_japanese": titles.get("ja_jp") or "",
                    "poster": poster.get("original") or poster.get("large") or "",
                    "banner": cover.get("original") or cover.get("large") or "",
                    "synopsis": synopsis,
                    "genres": [],
                    "themes": [],
                    "score": float(attrs.get("averageRating") or 0) if attrs.get("averageRating") else None,
                    "episodes": attrs.get("episodeCount"),
                    "duration": attrs.get("episodeLength"),
                    "status": attrs.get("status"),
                    "type": attrs.get("showType"),
                    "source": "Kitsu",
                    "mal_id": None,
                    "url": f"https://kitsu.io/anime/{anime.get('id')}" if anime.get("id") else "",
                    "characters": [],
                    "studios": [],
                    "producers": [],
                    "external_links": [],
                    "streaming": [],
                    "relations": [],
                }
                log.info("Kitsu detail OK for '%s'", title)
                return result
            except Exception as e:
                log.debug("Kitsu detail failed for '%s': %s", title, e)

            return {"error": "Anime not found in any source"}

    except Exception as exc:
        log.error("Anime detail fetch failed: %s", exc)
        return {"error": str(exc)}


async def fetch_anime_detail_legacy(
    title: str,
    content_filter: str = "sfw",
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Legacy Jikan-only detail fetch (kept as reference)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
            params = {"q": title, "limit": 1}
            if content_filter == "sfw":
                params["sfw"] = "true"
            elif content_filter == "nsfw":
                params["rating"] = "rx"
            response = await client.get(
                "https://api.jikan.moe/v4/anime",
                params=params,
            )
            if response.status_code == 429:
                await asyncio.sleep(1.0)
                response = await client.get(
                    "https://api.jikan.moe/v4/anime",
                    params=params,
                )
            response.raise_for_status()
            data = response.json()
            anime = (data.get("data") or [{}])[0]
            if not anime:
                return {"error": "Anime not found"}

            mal_id = anime.get("mal_id")
            images = anime.get("images", {}).get("jpg", {}) | anime.get("images", {}).get("webp", {})
            raw_poster = (images.get("large_image_url") or images.get("image_url") or "").replace("http://", "https://")
            raw_banner = (anime.get("images", {}).get("jpg", {}).get("large_image_url") or "").replace("http://", "https://")
            genres = [g.get("name") for g in anime.get("genres", []) if g.get("name")]
            themes_list = [t.get("name") for t in anime.get("themes", []) if t.get("name")]
            demographics = [d.get("name") for d in anime.get("demographics", []) if d.get("name")]

            result = {
                "title": anime.get("title"),
                "title_japanese": anime.get("title_japanese"),
                "title_english": anime.get("title_english"),
                "title_synonyms": anime.get("title_synonyms", []),
                "synopsis": anime.get("synopsis"),
                "background": anime.get("background"),
                "poster": raw_poster,
                "banner": raw_banner,
                "trailer": anime.get("trailer", {}).get("url"),
                "trailer_embed": anime.get("trailer", {}).get("embed_url"),
                "score": anime.get("score"),
                "scored_by": anime.get("scored_by"),
                "rank": anime.get("rank"),
                "popularity": anime.get("popularity"),
                "members": anime.get("members"),
                "favorites": anime.get("favorites"),
                "episodes": anime.get("episodes"),
                "status": anime.get("status"),
                "aired": anime.get("aired", {}).get("string"),
                "rating": anime.get("rating"),
                "type": anime.get("type"),
                "source": anime.get("source"),
                "duration": anime.get("duration"),
                "rating_val": anime.get("rating"),
                "genres": genres,
                "themes": themes_list,
                "demographics": demographics,
                "studios": [s.get("name") for s in anime.get("studios", []) if s.get("name")],
                "producers": [p.get("name") for p in anime.get("producers", []) if p.get("name")][:5],
                "url": anime.get("url"),
                "mal_id": mal_id,
                "relations": [],
                "opening_themes": [],
                "ending_themes": [],
                "external_links": [],
                "streaming": [],
            }

            # Fetch full detail with relations, themes, external links
            if mal_id:
                try:
                    full_resp = await client.get(
                        f"https://api.jikan.moe/v4/anime/{mal_id}/full",
                        timeout=httpx.Timeout(8.0, connect=5.0),
                    )
                    if full_resp.status_code == 429:
                        await asyncio.sleep(1.0)
                        full_resp = await client.get(
                            f"https://api.jikan.moe/v4/anime/{mal_id}/full",
                            timeout=httpx.Timeout(8.0, connect=5.0),
                        )
                    if full_resp.status_code == 200:
                        full_data = full_resp.json().get("data", {})
                        if full_data:
                            result["relations"] = [
                                {
                                    "relation": r.get("relation", ""),
                                    "entries": [
                                        {
                                            "mal_id": e.get("mal_id"),
                                            "type": e.get("type", ""),
                                            "name": e.get("name", ""),
                                            "url": e.get("url", ""),
                                        }
                                        for e in r.get("entry", [])
                                    ],
                                }
                                for r in full_data.get("relations", [])
                            ]
                            result["opening_themes"] = [
                                t.strip() for t in full_data.get("theme", {}).get("openings", [])
                            ]
                            result["ending_themes"] = [
                                t.strip() for t in full_data.get("theme", {}).get("endings", [])
                            ]
                            result["external_links"] = [
                                {"name": e.get("name"), "url": e.get("url")}
                                for e in full_data.get("external", [])
                            ]
                            result["streaming"] = [
                                {"name": s.get("name"), "url": s.get("url")}
                                for s in full_data.get("streaming", [])
                            ]
                except Exception:
                    pass

            return result

    except Exception as e:
        return {"error": str(e)}


# Explore cache: short-lived so the page feels instant on repeat visits.
_EXPLORE_CACHE: dict[str, Any] = {}
_EXPLORE_CACHE_TTL = 1800  # 30 minutes


async def fetch_explore(content_filter: str = "sfw", timeout: float = 12.0) -> dict[str, Any]:
    """Fetch top/trending/seasonal anime in parallel for the Explore page.

    Returns a dict with sections: season_now, top_airing, top_tv, upcoming, popular.
    Each section is a list of normalized anime cards {title, poster, score, ...}.
    """
    now = time.time()
    cached = _EXPLORE_CACHE.get(content_filter)
    if cached and now - cached.get("_ts", 0) < _EXPLORE_CACHE_TTL:
        return {k: v for k, v in cached.items() if k != "_ts"}

    base = "https://api.jikan.moe/v4"
    endpoints = {
        "season_now": f"{base}/seasons/now",
        "top_airing": f"{base}/top/anime",
        "top_tv": f"{base}/top/anime?type=tv",
        "upcoming": f"{base}/seasons/upcoming",
        "popular": f"{base}/top/anime?filter=bypopularity",
    }

    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        images = item.get("images", {}).get("jpg", {}) or {}
        poster = (images.get("large_image_url") or images.get("image_url") or "").replace("http://", "https://")
        return {
            "mal_id": item.get("mal_id"),
            "title": item.get("title") or item.get("title_english") or "Unknown",
            "title_english": item.get("title_english"),
            "poster": poster,
            "score": item.get("score"),
            "episodes": item.get("episodes"),
            "type": item.get("type") or "TV",
            "status": item.get("status"),
            "synopsis": (item.get("synopsis") or "")[:240],
            "genres": [g.get("name") for g in item.get("genres", []) if g.get("name")][:5],
            "year": item.get("year"),
            "url": item.get("url"),
            "rating": None,
            "similarity_percentage": None,
        }

    semaphore = asyncio.Semaphore(2)
    results: dict[str, list[dict[str, Any]]] = {}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
        for section, url in endpoints.items():
            async with semaphore:
                try:
                    # sfw filter for browse-friendly results
                    params = {"limit": 12}
                    if content_filter == "sfw":
                        params["sfw"] = "true"
                    resp = await client.get(url, params=params)
                    if resp.status_code == 429:
                        await asyncio.sleep(1.2)
                        resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json().get("data", []) or []
                    results[section] = [_normalize(d) for d in data[:12]]
                except Exception:
                    results[section] = []
                # Small delay to respect Jikan rate limits between sections
                await asyncio.sleep(0.35)

    payload = {**results, "_ts": now}
    _EXPLORE_CACHE[content_filter] = payload
    return results


# ── Database Helper Functions ──────────────────────────────────────────
# AniList GraphQL
ANILIST_GRAPHQL = "https://graphql.anilist.co"
ANILIST_HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "SmartFox/1.0"}
JIKAN_BASE = "https://api.jikan.moe/v4"
KITSU_BASE = "https://kitsu.io/api/edge"
KITSU_HEADERS = {"Accept": "application/vnd.api+json"}
ANIDB_BASE = "http://api.anidb.net:9001/httpapi"
ANIDB_CLIENT = "smartfox"
ANIDB_CLIENTVER = 1
ANIDB_PROTOVER = 1


def _db_anilist_post(query: str, variables: dict) -> dict:
    """Post to AniList GraphQL API."""
    resp = httpx.post(
        ANILIST_GRAPHQL,
        json={"query": query, "variables": variables},
        headers=ANILIST_HEADERS,
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def _db_anilist_anime(q: str, page: int, per_page: int) -> dict:
    """Search anime on AniList."""
    query = """
      query ($search: String, $page: Int, $perPage: Int) {
        Page(page: $page, perPage: $perPage) {
          media(search: $search, type: ANIME, sort: POPULARITY_DESC) {
            id title { romaji english } coverImage { large } format status
            averageScore popularity episodes genres
          }
          pageInfo { total lastPage hasNextPage }
        }
      }
    """
    data = _db_anilist_post(query, {"search": q, "page": page, "per_page": per_page})
    page_data = data.get("data", {}).get("Page", {})
    media = page_data.get("media", [])
    items = [
        {
            "id": m["id"], "title": m["title"].get("romaji") or m["title"].get("english") or "Unknown",
            "poster": m["coverImage"].get("large", ""), "type": m.get("format", "TV"),
            "status": m.get("status"), "score": m.get("averageScore"), "popularity": m.get("popularity"),
            "episodes": m.get("episodes"), "genres": m.get("genres", []),
        }
        for m in media
    ]
    return {"items": items, "total": page_data.get("pageInfo", {}).get("total", 0), "source": "AniList"}


def _db_anilist_anime_by_id(anilist_id: int) -> dict:
    """Get anime detail by AniList ID."""
    query = """
      query ($id: Int) {
        Media(id: $id, type: ANIME) {
          id title { romaji english native } description(asHtml: false)
          coverImage { large } bannerImage format status episodes duration
          genres tags { name } averageScore meanScore popularity favourites
          nextAiringEpisode { episode airingAt }
          characters(sort: ROLE, perPage: 20) { edges { role node { id name { full } image { medium } } voiceActors { id name { full } image { medium } } } }
          studios(isMain: true) { nodes { id name } }
          producers { nodes { name } }
          relations { edges { relationType node { id title { romaji } type } } }
          ExternalLinks { site url }
          streamingEpisodes { title thumbnail url site }
        }
      }
    """
    data = _db_anilist_post(query, {"id": anilist_id})
    m = data.get("data", {}).get("Media")
    if not m:
        return {"error": "Anime not found"}
    return {
        "id": m["id"],
        "title": m["title"].get("romaji") or "",
        "title_english": m["title"].get("english") or "",
        "title_japanese": m["title"].get("native") or "",
        "synopsis": (m.get("description") or "").replace("<br>", "\n").strip()[:500],
        "poster": m.get("coverImage", {}).get("large", ""),
        "banner": m.get("bannerImage", ""),
        "type": m.get("format"),
        "status": m.get("status"),
        "episodes": m.get("episodes"),
        "duration": m.get("duration"),
        "genres": m.get("genres", []),
        "themes": [t["name"] for t in (m.get("tags") or [])][:10],
        "score": m.get("averageScore"),
        "mean_score": m.get("meanScore"),
        "popularity": m.get("popularity"),
        "favorites": m.get("favourites"),
        "next_episode": m.get("nextAiringEpisode"),
        "characters": [
            {
                "name": e["node"]["name"]["full"],
                "image": e["node"]["image"]["medium"],
                "role": e.get("role", ""),
                "voice_actor": e.get("voiceActors", [{}])[0]["name"]["full"] if e.get("voiceActors") else "",
            }
            for e in (m.get("characters", {}).get("edges") or [])
        ],
        "studios": [s["name"] for s in (m.get("studios", {}).get("nodes") or [])],
        "producers": [p["name"] for p in (m.get("producers", {}).get("nodes") or [])],
        "relations": [
            {"relation": e.get("relationType"), "name": e["node"]["title"].get("romaji"), "type": e["node"].get("type")}
            for e in (m.get("relations", {}).get("edges") or [])
        ],
        "external_links": [{"site": l.get("site"), "url": l.get("url")} for l in (m.get("ExternalLinks") or []) if l.get("url")],
        "streaming": [{"title": e.get("title"), "url": e.get("url"), "site": e.get("site")} for e in (m.get("streamingEpisodes") or [])],
        "source": "AniList",
    }


def _db_jikan_anime(q: str, page: int) -> dict:
    """Search anime on Jikan."""
    resp = httpx.get(f"{JIKAN_BASE}/anime", params={"q": q, "page": page, "sfw": "true", "limit": 25}, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    items = []
    for a in data.get("data", []):
        images = a.get("images", {}).get("jpg", {})
        items.append({
            "id": a.get("mal_id"), "title": a.get("title"),
            "poster": (images.get("large_image_url") or images.get("image_url", "")).replace("http://", "https://"),
            "type": a.get("type"), "status": a.get("status"),
            "score": a.get("score"), "episodes": a.get("episodes"),
            "genres": [g["name"] for g in a.get("genres", [])],
        })
    return {"items": items, "total": data.get("pagination", {}).get("last_visible_page", 1) * 25, "source": "Jikan"}


def _db_jikan_anime_by_id(mal_id: int) -> dict:
    """Get anime detail by MAL ID."""
    resp = httpx.get(f"{JIKAN_BASE}/anime/{mal_id}", timeout=10.0)
    resp.raise_for_status()
    a = resp.json().get("data", {})
    images = a.get("images", {}).get("jpg", {})
    return {
        "id": a.get("mal_id"), "title": a.get("title"),
        "title_english": a.get("title_english") or "",
        "synopsis": (a.get("synopsis") or "")[:500],
        "poster": (images.get("large_image_url") or images.get("image_url", "")).replace("http://", "https://"),
        "type": a.get("type"), "status": a.get("status"),
        "episodes": a.get("episodes"), "duration": a.get("duration"),
        "score": a.get("score"), "popularity": a.get("popularity"),
        "favorites": a.get("favorites"),
        "genres": [g["name"] for g in a.get("genres", [])],
        "themes": [t["name"] for t in a.get("themes", [])],
        "studios": [s["name"] for s in a.get("studios", [])],
        "producers": [p["name"] for p in a.get("producers", [])],
        "url": a.get("url", ""),
        "source": "Jikan",
    }


def _db_kitsu_anime(q: str, page: int) -> dict:
    """Search anime on Kitsu."""
    offset = (page - 1) * 20
    resp = httpx.get(
        f"{KITSU_BASE}/anime",
        params={"filter[text]": q, "page[offset]": offset, "page[limit]": 20},
        headers=KITSU_HEADERS,
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    items = []
    for a in data.get("data", []):
        attrs = a.get("attributes", {})
        poster = attrs.get("posterImage") or {}
        items.append({
            "id": a.get("id"), "title": attrs.get("canonicalTitle"),
            "poster": poster.get("original") or poster.get("large", ""),
            "type": attrs.get("showType"), "status": attrs.get("status"),
            "score": float(attrs.get("averageRating") or 0) if attrs.get("averageRating") else None,
            "episodes": attrs.get("episodeCount"),
        })
    return {"items": items, "source": "Kitsu"}


def _db_kitsu_anime_by_id(kitsu_id: str) -> dict:
    """Get anime detail by Kitsu ID."""
    resp = httpx.get(f"{KITSU_BASE}/anime/{kitsu_id}", headers=KITSU_HEADERS, timeout=10.0)
    resp.raise_for_status()
    a = resp.json().get("data", {})
    attrs = a.get("attributes", {})
    titles = attrs.get("titles", {})
    poster = attrs.get("posterImage") or {}
    cover = attrs.get("coverImage") or {}
    return {
        "id": a.get("id"), "title": attrs.get("canonicalTitle"),
        "title_english": titles.get("en", ""),
        "title_japanese": titles.get("ja_jp", ""),
        "synopsis": (attrs.get("synopsis") or "")[:500],
        "poster": poster.get("original") or poster.get("large", ""),
        "banner": cover.get("original") or cover.get("large", ""),
        "type": attrs.get("showType"), "status": attrs.get("status"),
        "episodes": attrs.get("episodeCount"), "duration": attrs.get("episodeLength"),
        "score": float(attrs.get("averageRating") or 0) if attrs.get("averageRating") else None,
        "url": f"https://kitsu.io/anime/{a.get('id')}",
        "source": "Kitsu",
    }


def _db_anilist_manga(q: str, page: int, per_page: int) -> dict:
    """Search manga on AniList."""
    query = """
      query ($search: String, $page: Int, $perPage: Int) {
        Page(page: $page, perPage: $perPage) {
          media(search: $search, type: MANGA, sort: POPULARITY_DESC) {
            id title { romaji english } coverImage { large } format status
            averageScore popularity chapters volumes genres
          }
          pageInfo { total lastPage hasNextPage }
        }
      }
    """
    data = _db_anilist_post(query, {"search": q, "page": page, "per_page": per_page})
    page_data = data.get("data", {}).get("Page", {})
    media = page_data.get("media", [])
    items = [
        {
            "id": m["id"], "title": m["title"].get("romaji") or m["title"].get("english") or "Unknown",
            "poster": m["coverImage"].get("large", ""), "type": m.get("format", "Manga"),
            "status": m.get("status"), "score": m.get("averageScore"), "popularity": m.get("popularity"),
            "chapters": m.get("chapters"), "volumes": m.get("volumes"), "genres": m.get("genres", []),
        }
        for m in media
    ]
    return {"items": items, "total": page_data.get("pageInfo", {}).get("total", 0), "source": "AniList"}


def _db_anilist_manga_by_id(anilist_id: int) -> dict:
    """Get manga detail by AniList ID."""
    query = """
      query ($id: Int) {
        Media(id: $id, type: MANGA) {
          id title { romaji english native } description(asHtml: false)
          coverImage { large } bannerImage format status chapters volumes
          genres tags { name } averageScore meanScore popularity favourites
          characters(sort: ROLE, perPage: 20) { edges { role node { id name { full } image { medium } } } }
          relations { edges { relationType node { id title { romaji } type } } }
          ExternalLinks { site url }
        }
      }
    """
    data = _db_anilist_post(query, {"id": anilist_id})
    m = data.get("data", {}).get("Media")
    if not m:
        return {"error": "Manga not found"}
    return {
        "id": m["id"],
        "title": m["title"].get("romaji") or "",
        "title_english": m["title"].get("english") or "",
        "title_japanese": m["title"].get("native") or "",
        "synopsis": (m.get("description") or "").replace("<br>", "\n").strip()[:500],
        "poster": m.get("coverImage", {}).get("large", ""),
        "type": m.get("format"), "status": m.get("status"),
        "chapters": m.get("chapters"), "volumes": m.get("volumes"),
        "genres": m.get("genres", []),
        "themes": [t["name"] for t in (m.get("tags") or [])][:10],
        "score": m.get("averageScore"), "mean_score": m.get("meanScore"),
        "popularity": m.get("popularity"), "favorites": m.get("favourites"),
        "characters": [
            {"name": e["node"]["name"]["full"], "image": e["node"]["image"]["medium"], "role": e.get("role")}
            for e in (m.get("characters", {}).get("edges") or [])
        ],
        "relations": [
            {"relation": e.get("relationType"), "name": e["node"]["title"].get("romaji"), "type": e["node"].get("type")}
            for e in (m.get("relations", {}).get("edges") or [])
        ],
        "external_links": [{"site": l.get("site"), "url": l.get("url")} for l in (m.get("ExternalLinks") or []) if l.get("url")],
        "source": "AniList",
    }


def _db_kitsu_manga(q: str, page: int) -> dict:
    """Search manga on Kitsu."""
    offset = (page - 1) * 20
    resp = httpx.get(
        f"{KITSU_BASE}/manga",
        params={"filter[text]": q, "page[offset]": offset, "page[limit]": 20},
        headers=KITSU_HEADERS,
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    items = []
    for m in data.get("data", []):
        attrs = m.get("attributes", {})
        poster = attrs.get("posterImage") or {}
        items.append({
            "id": m.get("id"), "title": attrs.get("canonicalTitle"),
            "poster": poster.get("original") or poster.get("large", ""),
            "type": attrs.get("mangaType"), "status": attrs.get("status"),
            "score": float(attrs.get("averageRating") or 0) if attrs.get("averageRating") else None,
            "chapters": attrs.get("chapterCount"),
        })
    return {"items": items, "source": "Kitsu"}


def _db_kitsu_manga_by_id(kitsu_id: str) -> dict:
    """Get manga detail by Kitsu ID."""
    resp = httpx.get(f"{KITSU_BASE}/manga/{kitsu_id}", headers=KITSU_HEADERS, timeout=10.0)
    resp.raise_for_status()
    m = resp.json().get("data", {})
    attrs = m.get("attributes", {})
    titles = attrs.get("titles", {})
    poster = attrs.get("posterImage") or {}
    cover = attrs.get("coverImage") or {}
    return {
        "id": m.get("id"), "title": attrs.get("canonicalTitle"),
        "title_english": titles.get("en", ""),
        "title_japanese": titles.get("ja_jp", ""),
        "synopsis": (attrs.get("synopsis") or "")[:500],
        "poster": poster.get("original") or poster.get("large", ""),
        "banner": cover.get("original") or cover.get("large", ""),
        "type": attrs.get("mangaType"), "status": attrs.get("status"),
        "chapters": attrs.get("chapterCount"), "volumes": attrs.get("volumeCount"),
        "score": float(attrs.get("averageRating") or 0) if attrs.get("averageRating") else None,
        "url": f"https://kitsu.io/manga/{m.get('id')}",
        "source": "Kitsu",
    }


def _db_anilist_studios(q: str, page: int) -> dict:
    """Search studios on AniList."""
    query = """
      query ($search: String, $page: Int) {
        Page(page: $page, perPage: 25) {
          studios(search: $search, sort: FAVOURITES_DESC) {
            id name isAnimationStudio favourites
          }
          pageInfo { total lastPage hasNextPage }
        }
      }
    """
    data = _db_anilist_post(query, {"search": q, "page": page})
    page_data = data.get("data", {}).get("Page", {})
    studios = page_data.get("studios", [])
    items = [
        {"id": s["id"], "name": s["name"], "is_animation": s.get("isAnimationStudio"), "favorites": s.get("favourites")}
        for s in studios
    ]
    return {"items": items, "total": page_data.get("pageInfo", {}).get("total", 0), "source": "AniList"}


def _db_anilist_studio_by_id(studio_id: int) -> dict:
    """Get studio detail and its anime."""
    query = """
      query ($id: Int) {
        Studio(id: $id) {
          id name isAnimationStudio favourites
          media(sort: POPULARITY_DESC, perPage: 50) {
            edges { node { id title { romaji english } coverImage { large } averageScore popularity episodes format status } }
          }
        }
      }
    """
    data = _db_anilist_post(query, {"id": studio_id})
    s = data.get("data", {}).get("Studio")
    if not s:
        return {"error": "Studio not found"}
    anime = [
        {
            "id": e["node"]["id"], "title": e["node"]["title"].get("romaji") or "",
            "poster": e["node"]["coverImage"].get("large", ""),
            "score": e["node"].get("averageScore"), "popularity": e["node"].get("popularity"),
            "episodes": e["node"].get("episodes"), "type": e["node"].get("format"),
            "status": e["node"].get("status"),
        }
        for e in (s.get("media", {}).get("edges") or [])
    ]
    return {
        "id": s["id"], "name": s["name"],
        "is_animation": s.get("isAnimationStudio"),
        "favorites": s.get("favourites"),
        "anime": anime,
        "source": "AniList",
    }


def _db_anilist_characters(q: str, page: int) -> dict:
    """Search characters on AniList."""
    query = """
      query ($search: String, $page: Int) {
        Page(page: $page, perPage: 25) {
          characters(search: $search, sort: FAVOURITES_DESC) {
            id name { full } image { medium } favourites
          }
          pageInfo { total lastPage hasNextPage }
        }
      }
    """
    data = _db_anilist_post(query, {"search": q, "page": page})
    page_data = data.get("data", {}).get("Page", {})
    chars = page_data.get("characters", [])
    items = [
        {"id": c["id"], "name": c["name"].get("full"), "image": c.get("image", {}).get("medium", ""), "favorites": c.get("favourites")}
        for c in chars
    ]
    return {"items": items, "total": page_data.get("pageInfo", {}).get("total", 0), "source": "AniList"}


def _db_anilist_character_by_id(char_id: int) -> dict:
    """Get character detail."""
    query = """
      query ($id: Int) {
        Character(id: $id) {
          id name { full } image { large } description favourites
          media(sort: POPULARITY_DESC, perPage: 10) {
            edges { role node { id title { romaji english } coverImage { large } type } }
          }
        }
      }
    """
    data = _db_anilist_post(query, {"id": char_id})
    c = data.get("data", {}).get("Character")
    if not c:
        return {"error": "Character not found"}
    anime = [
        {
            "id": e["node"]["id"], "title": e["node"]["title"].get("romaji"),
            "poster": e["node"]["coverImage"].get("large", ""),
            "role": e.get("role"), "type": e["node"].get("type"),
        }
        for e in (c.get("media", {}).get("edges") or [])
    ]
    return {
        "id": c["id"], "name": c["name"].get("full"),
        "image": c.get("image", {}).get("large", ""),
        "description": (c.get("description") or "")[:500],
        "favorites": c.get("favourites"),
        "anime": anime,
        "source": "AniList",
    }


def _db_kitsu_characters(q: str, page: int) -> dict:
    """Search characters on Kitsu."""
    offset = (page - 1) * 20
    resp = httpx.get(
        f"{KITSU_BASE}/characters",
        params={"filter[name]": q, "page[offset]": offset, "page[limit]": 20},
        headers=KITSU_HEADERS,
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    items = []
    for c in data.get("data", []):
        attrs = c.get("attributes", {})
        items.append({
            "id": c.get("id"), "name": attrs.get("canonicalName"),
            "image": (attrs.get("image") or {}).get("original", ""),
        })
    return {"items": items, "source": "Kitsu"}


def _db_kitsu_character_by_id(kitsu_id: str) -> dict:
    """Get character detail by Kitsu ID."""
    resp = httpx.get(f"{KITSU_BASE}/characters/{kitsu_id}", headers=KITSU_HEADERS, timeout=10.0)
    resp.raise_for_status()
    c = resp.json().get("data", {})
    attrs = c.get("attributes", {})
    return {
        "id": c.get("id"),
        "name": attrs.get("canonicalName"),
        "image": (attrs.get("image") or {}).get("original", ""),
        "description": (attrs.get("description") or "")[:500],
        "source": "Kitsu",
    }


# ── AniDB HTTP XML API ───────────────────────────────────────────────
def _db_anidb_request(request: str, params: dict) -> str:
    """Make a request to AniDB HTTP API. Returns raw XML string."""
    all_params = {
        "client": ANIDB_CLIENT,
        "clientver": ANIDB_CLIENTVER,
        "protover": ANIDB_PROTOVER,
        "request": request,
        **params,
    }
    resp = httpx.get(ANIDB_BASE, params=all_params, timeout=10.0)
    resp.raise_for_status()
    return resp.text


def _db_anidb_search(q: str) -> list[dict]:
    """Search anime on AniDB. Returns list of {id, title}."""
    xml_text = _db_anidb_request("anime", {"aid": q} if q.isdigit() else {})
    # AniDB search is limited; we parse the XML response
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # If single anime result
    if root.tag == "anime":
        aid = root.get("id")
        titles = root.find("titles")
        main_title = ""
        if titles is not None:
            for t in titles.findall("title"):
                if t.get("type") == "main":
                    main_title = t.text or ""
                    break
            if not main_title:
                t0 = titles.find("title")
                main_title = t0.text if t0 is not None else "Unknown"
        return [{"id": aid, "title": main_title}]
    return []


def _db_anidb_anime_by_id(aid: str) -> dict:
    """Get anime detail by AniDB ID."""
    import xml.etree.ElementTree as ET
    xml_text = _db_anidb_request("anime", {"aid": aid})
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"error": "Failed to parse AniDB response"}

    if root.tag != "anime":
        return {"error": "Anime not found on AniDB"}

    # Parse titles
    titles = root.find("titles")
    main_title = ""
    en_title = ""
    jp_title = ""
    if titles is not None:
        for t in titles.findall("title"):
            lang = t.get("xml:lang", "")
            ttype = t.get("type", "")
            text = t.text or ""
            if ttype == "main":
                main_title = text
            elif lang == "en" and ttype == "official":
                en_title = text
            elif lang == "ja" and ttype == "official":
                jp_title = text
        if not main_title:
            t0 = titles.find("title")
            main_title = t0.text if t0 is not None else "Unknown"

    # Parse descriptions
    description_el = root.find("description")
    synopsis = ""
    if description_el is not None and description_el.text:
        # Clean up AniDB description format
        import re
        synopsis = re.sub(r'\[.*?\]', '', description_el.text).strip()[:500]

    # Parse related anime
    relations = []
    related = root.find("relatedanime")
    if related is not None:
        for ra in related.findall("anime"):
            relations.append({
                "relation": ra.get("type", ""),
                "name": ra.text or "",
                "type": "Anime",
                "id": ra.get("id"),
            })

    # Parse similar anime
    similar = root.find("similaranime")
    similar_list = []
    if similar is not None:
        for sa in similar.findall("anime"):
            similar_list.append({
                "name": sa.text or "",
                "approval": sa.get("approval"),
                "total": sa.get("total"),
            })

    # Parse recommendations
    recs = root.find("recommendations")
    rec_list = []
    if recs is not None:
        for rec in recs.findall("recommendation"):
            rec_list.append({
                "name": rec.text or "",
                "total": rec.get("total"),
            })

    # Parse tags
    tags_el = root.find("tags")
    genres = []
    themes = []
    if tags_el is not None:
        for tag in tags_el.findall("tag"):
            tag_id = tag.get("id")
            tag_name = tag.find("name")
            name = tag_name.text if tag_name is not None else ""
            # AniDB tags with id <= 7 are genres
            try:
                tid = int(tag_id) if tag_id else 0
            except ValueError:
                tid = 0
            if 1 <= tid <= 7:
                genres.append(name)
            elif name:
                themes.append(name)

    # Get episode info
    episode_count_el = root.find("episodecount")
    episode_count = None
    if episode_count_el is not None and episode_count_el.text:
        try:
            episode_count = int(episode_count_el.text)
        except ValueError:
            pass

    # Type
    type_el = root.find("type")
    anime_type = type_el.text if type_el is not None else ""

    # Dates
    start_el = root.find("startdate")
    start_date = start_el.text if start_el is not None else ""

    # Rating
    ratings = root.find("ratings")
    score = None
    if ratings is not None:
        permanent = ratings.find("permanent")
        if permanent is not None and permanent.text:
            try:
                score = round(float(permanent.text) * 10)  # Convert 0-10 to 0-100
            except ValueError:
                pass

    # URL
    url = f"https://anidb.net/anime/{aid}"

    # Poster (AniDB doesn't provide images via HTTP API, use URL)
    poster_url = f"https://cdn.anidb.net/images/main/{aid}.jpg"

    return {
        "id": int(aid) if aid.isdigit() else aid,
        "title": main_title,
        "title_english": en_title,
        "title_japanese": jp_title,
        "synopsis": synopsis,
        "poster": poster_url,
        "banner": "",
        "type": anime_type,
        "status": "Unknown",
        "episodes": episode_count,
        "duration": None,
        "genres": genres,
        "themes": themes[:10],
        "score": score,
        "start_date": start_date,
        "relations": relations,
        "similar": similar_list[:10],
        "recommendations": rec_list[:10],
        "url": url,
        "source": "AniDB",
    }


def _db_anidb_search_anime(q: str, page: int) -> dict:
    """Search anime on AniDB (limited search via HTTP API)."""
    # AniDB HTTP API doesn't support text search directly
    # We return an error message suggesting to use AniList/Jikan for search
    return {
        "items": [],
        "total": 0,
        "source": "AniDB",
        "message": "AniDB HTTP API does not support text search. Use AniList or Jikan for searching, then lookup by AniDB ID.",
    }


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="anime_search/templates",
        static_folder="anime_search/static",
    )
    settings = load_settings()
    session_name = auto_init_session(settings)
    log.info("Active session: %s", session_name)
    engine = AnimeSearchEngine(settings)

    @app.get("/")
    def index() -> str:
        return render_template("index.html", query="", profile=None, error=None, recommendation=None)

    @app.get("/api/config")
    def api_config_get() -> Any:
        d = engine.settings.to_dict(include_secrets=False)
        return jsonify(d)

    @app.post("/api/config")
    def api_config_post() -> Any:
        nonlocal settings, engine
        payload = request.get_json(silent=True) or {}
        from anime_search.config import SENSITIVE_KEYS
        for key in SENSITIVE_KEYS:
            payload.pop(key, None)
        try:
            # Merge incoming changes onto the current settings so omitted
            # fields keep their existing values instead of reverting to defaults.
            current = engine.settings.to_dict(include_secrets=True)
            current.update(payload)
            new_settings = engine.settings.from_dict(current)
            validation_error = new_settings.validate_ai_provider()
            if validation_error:
                return jsonify({"error": validation_error}), 400
            settings = new_settings
            save_settings(settings)
            engine = AnimeSearchEngine(settings)
            active = get_active_session_name()
            if not active:
                from anime_search.config import auto_init_session as _ais
                active = _ais(settings)
            save_session(active, settings)
            return jsonify({"status": "ok", "config": engine.settings.to_dict(include_secrets=False)})
        except Exception as exc:
            log.error("Config update failed: %s", exc)
            return jsonify({"error": str(exc)}), 400

    @app.post("/search")
    def search() -> str:
        query = request.form.get("query", "").strip()
        description = request.form.get("description", "").strip()
        content_filter = request.form.get("content_filter", "sfw").strip()
        negative_prompt = request.form.get("negative_prompt", "").strip()
        if content_filter not in ("sfw", "nsfw", "all"):
            content_filter = "sfw"
        if not query and not description:
            return render_template("index.html", query=query, profile=None, error="Enter an anime title or description.", recommendation=None), 400

        if not query and description:
            query = description[:80]

        try:
            profile = run_async(engine.search(query, description, content_filter, negative_prompt))
            recommendation = run_async(engine.recommend(query, description, content_filter, negative_prompt))
            top_recommendations = (recommendation or {}).get("top_50", []) if recommendation else []
            return render_template(
                "index.html",
                query=query,
                profile=profile.model_dump(mode="json"),
                profile_json=profile.model_dump_json(indent=2),
                error=None,
                recommendation=recommendation,
                top_recommendations=top_recommendations,
                recommendation_json=json.dumps(recommendation, ensure_ascii=False, indent=2) if recommendation else None,
                description=description,
                content_filter=content_filter,
                negative_prompt=negative_prompt,
            )
        except Exception as exc:
            log.error("Search failed for '%s': %s", query, exc)
            return render_template("index.html", query=query, profile=None, error=str(exc), recommendation=None, description=description, content_filter=content_filter, negative_prompt=negative_prompt), 502

    @app.post("/api/ai/start")
    def api_ai_start() -> Any:
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query") or "").strip()
        description = str(payload.get("description") or "").strip()
        content_filter = str(payload.get("content_filter") or "sfw").strip()
        negative_prompt = str(payload.get("negative_prompt") or "").strip()
        if content_filter not in ("sfw", "nsfw", "all"):
            content_filter = "sfw"
        if not query and not description:
            return jsonify({"error": "Missing query or description."}), 400
        if not query:
            query = description[:80]
        cleanup_old_tasks()
        task_id = engine.start_background_recommend(query, description, content_filter, negative_prompt)
        return jsonify({"task_id": task_id, "status": "started"})

    @app.get("/api/ai/status/<task_id>")
    def api_ai_status(task_id: str) -> Any:
        task = _get_task(task_id)
        if task is None:
            return jsonify({"error": "Task not found."}), 404
        recommendation = task.get("recommendation")
        if recommendation:
            try:
                json.dumps(recommendation, default=str)
            except Exception:
                recommendation = {
                    "top_50": task.get("results", [])[:50],
                    "source_title": recommendation.get("source_title", ""),
                    "engine": recommendation.get("engine", "agent"),
                }
        _record_if_usage(recommendation)
        return jsonify({
            "task_id": task_id,
            "status": task.get("status", "unknown"),
            "progress": task.get("progress", 0),
            "message": task.get("message", ""),
            "results": task.get("results", []),
            "error": task.get("error"),
            "profile": task.get("profile"),
            "recommendation": recommendation,
            "tool_calls": task.get("tool_calls", []),
            "system_status": task.get("system_status", {}),
        })

    @app.get("/api/ai/stream/<task_id>")
    def api_ai_stream(task_id: str) -> Response:
        def generate():
            last_update = 0
            while True:
                task = _get_task(task_id)
                if task is None:
                    yield f"data: {json.dumps({'error': 'Task not found'})}\n\n"
                    return
                status = task.get("status", "unknown")
                progress = task.get("progress", 0)
                message = task.get("message", "")
                results = task.get("results", [])
                raw_text = task.get("raw_text", "")
                commentary = task.get("commentary", [])
                system_status = task.get("system_status", {})
                now = time.time()
                if now - last_update >= 0.2 or status in ("done", "error"):
                    payload = {
                        "status": status,
                        "progress": progress,
                        "message": message,
                        "count": len(results),
                    }
                    if results:
                        payload["latest"] = results[-1]
                    if raw_text:
                        payload["raw_length"] = len(raw_text)
                    if commentary:
                        payload["commentary"] = commentary[-20:]
                    if system_status:
                        payload["system_status"] = system_status
                    if status == "done":
                        recommendation = task.get("recommendation")
                        if recommendation:
                            try:
                                json.dumps(recommendation, default=str)
                                _record_if_usage(recommendation)
                                payload["recommendation"] = recommendation
                            except Exception:
                                payload["recommendation"] = {
                                    "top_50": results[:50],
                                    "source_title": recommendation.get("source_title", ""),
                                    "engine": recommendation.get("engine", "agent"),
                                }
                    try:
                        yield f"data: {json.dumps(payload, default=str)}\n\n"
                    except Exception as exc:
                        log.warning("SSE serialization failed: %s", exc)
                        safe_payload = {k: v for k, v in payload.items() if k != "recommendation"}
                        safe_payload["recommendation"] = {"top_50": results[:50]}
                        yield f"data: {json.dumps(safe_payload, default=str)}\n\n"
                    last_update = now
                if status in ("done", "error"):
                    return
                time.sleep(0.2)

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/ai/cancel/<task_id>")
    def api_ai_cancel(task_id: str) -> Any:
        if cancel_task(task_id):
            return jsonify({"status": "cancelled", "task_id": task_id})
        return jsonify({"error": "Task not found or already finished."}), 404

    @app.get("/api/sessions")
    def api_sessions_list() -> Any:
        return jsonify({"sessions": list_sessions(), "active": get_active_session_name()})

    @app.post("/api/sessions/save")
    def api_sessions_save() -> Any:
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name", "")).strip()
        if not name:
            return jsonify({"error": "Session name is required."}), 400
        label = str(payload.get("label", "")).strip()
        save_session(name, engine.settings, label)
        set_active_session_name(name)
        save_settings(engine.settings)
        return jsonify({"status": "ok", "name": name})

    @app.post("/api/sessions/load")
    def api_sessions_load() -> Any:
        nonlocal settings, engine
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name", "")).strip()
        if not name:
            return jsonify({"error": "Session name is required."}), 400
        session_settings = load_session(name)
        if session_settings is None:
            return jsonify({"error": f"Session '{name}' not found."}), 404
        settings = session_settings
        save_settings(settings)
        set_active_session_name(name)
        engine = AnimeSearchEngine(settings)
        return jsonify({"status": "ok", "config": engine.settings.to_dict(include_secrets=False), "session": name})

    @app.delete("/api/sessions/<name>")
    def api_sessions_delete(name: str) -> Any:
        if delete_session(name):
            return jsonify({"status": "deleted", "name": name})
        return jsonify({"error": f"Session '{name}' not found."}), 404

    @app.post("/api/sessions/rename")
    def api_sessions_rename() -> Any:
        payload = request.get_json(silent=True) or {}
        old_name = str(payload.get("old_name", "")).strip()
        new_name = str(payload.get("new_name", "")).strip()
        if not old_name or not new_name:
            return jsonify({"error": "old_name and new_name are required."}), 400
        if rename_session(old_name, new_name):
            return jsonify({"status": "renamed", "old_name": old_name, "new_name": new_name})
        return jsonify({"error": "Rename failed."}), 400

    @app.get("/api/anime/detail")
    def api_anime_detail() -> Any:
        title = request.args.get("title", "").strip()
        content_filter = request.args.get("content_filter", "sfw").strip()
        if not title:
            return jsonify({"error": "Missing title parameter."}), 400
        try:
            detail = run_async(fetch_anime_detail(title, content_filter))
            return jsonify(detail)
        except Exception as exc:
            log.error("Anime detail fetch failed: %s", exc)
            return jsonify({"error": str(exc)}), 502

    @app.post("/api/recommend/posters")
    def api_recommend_posters() -> Any:
        payload = request.get_json(silent=True) or {}
        titles = payload.get("titles") or []
        content_filter = payload.get("content_filter", "sfw")
        if not isinstance(titles, list) or not titles:
            return jsonify({"error": "Missing titles array."}), 400
        try:
            results = run_async(fetch_poster_batch([str(t) for t in titles[:50]], content_filter))
            return jsonify(results)
        except Exception as exc:
            log.error("Poster fetch failed: %s", exc)
            return jsonify({}), 200

    @app.get("/api/health")
    def api_health() -> Any:
        return jsonify({
            "status": "ok",
            "ai_provider": engine.settings.ai_provider,
            "ai_model": engine.settings.effective_ai_model,
            "ai_base_url": engine.settings.effective_ai_base_url,
            "lm_studio_url": engine.settings.local_ai_base_url,
            "model": engine.settings.local_ai_model,
            "cache": engine.cache.get_stats(),
            "rate_limits": engine._limiter.get_status(),
        })

    @app.get("/api/tokens/usage")
    def api_tokens_usage() -> Any:
        active = get_active_session_name()
        usage = load_token_usage()
        session_usage = usage.get(active, {}) if active else {}
        budget = engine.settings.token_budget
        total = session_usage.get("total_tokens", 0)
        return jsonify({
            "session": active or "",
            "prompt_tokens": session_usage.get("prompt_tokens", 0),
            "completion_tokens": session_usage.get("completion_tokens", 0),
            "total_tokens": total,
            "calls": session_usage.get("calls", 0),
            "budget": budget,
            "budget_used_pct": round(total / budget * 100, 1) if budget else 0.0,
        })

    @app.post("/api/test-connection")
    def api_test_connection() -> Any:
        import asyncio as _aio
        async def _test():
            url = engine.settings.effective_ai_base_url.rstrip("/") + "/v1/chat/completions"
            headers = engine.settings.openrouter_headers
            payload = {
                "model": engine.settings.effective_ai_model,
                "messages": [{"role": "user", "content": "Say hello in one word."}],
                "max_tokens": 10,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                return resp.status_code, resp.text[:1000]
        try:
            status, body = _aio.run(_test())
            return jsonify({"status": status, "body": body})
        except Exception as exc:
            return jsonify({"status": 0, "error": str(exc)}), 500

    @app.get("/api/tools")
    def api_tools() -> Any:
        from anime_search.tools import TOOL_DEFINITIONS
        return jsonify({"tools": TOOL_DEFINITIONS})

    @app.get("/api/models/status")
    def api_models_status() -> Any:
        import asyncio as _aio
        import time as _time

        settings = engine.settings
        if settings.ai_provider != "openrouter" or not settings.ai_api_key:
            return jsonify({"models": [], "error": "Not configured for OpenRouter"})

        models_to_check = [settings.openrouter_model] + [
            m.strip() for m in settings.openrouter_fallback_models.split(",") if m.strip()
        ]
        models_to_check = list(dict.fromkeys(models_to_check))

        async def _check():
            results = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as client:
                for model in models_to_check:
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 5,
                    }
                    t0 = _time.monotonic()
                    try:
                        resp = await client.post(
                            settings.effective_ai_base_url.rstrip("/") + "/v1/chat/completions",
                            json=payload,
                            headers=settings.llm_headers,
                        )
                        elapsed_ms = int((_time.monotonic() - t0) * 1000)
                        ok = resp.status_code == 200
                        rate_limited = resp.status_code == 429
                        results.append({
                            "model": model,
                            "status": "ok" if ok else ("rate_limited" if rate_limited else "error"),
                            "status_code": resp.status_code,
                            "latency_ms": elapsed_ms,
                            "is_primary": model == settings.openrouter_model,
                        })
                    except Exception as exc:
                        elapsed_ms = int((_time.monotonic() - t0) * 1000)
                        results.append({
                            "model": model,
                            "status": "error",
                            "status_code": 0,
                            "latency_ms": elapsed_ms,
                            "error": str(exc)[:200],
                            "is_primary": model == settings.openrouter_model,
                        })
            return results

        try:
            models = _aio.run(_check())
        except Exception as exc:
            return jsonify({"models": [], "error": str(exc)}), 500
        return jsonify({"models": models})

    @app.get("/api/system/status")
    def api_system_status() -> Any:
        return jsonify({
            "cache": engine.cache.get_stats(),
            "rate_limits": engine._limiter.get_status(),
            "poster_cache_size": len(POSTER_CACHE),
        })

    @app.get("/api/library")
    def api_library_list() -> Any:
        query = request.args.get("q", "").strip()
        status = request.args.get("status", "").strip()
        sort_by = request.args.get("sort", "added_at").strip()
        desc = request.args.get("desc", "1").strip() != "0"
        if query or status:
            entries = search_library(query, status)
            from anime_search.library import _sort_entries, _compute_stats
            entries = _sort_entries(entries, sort_by, desc)
            stats = _compute_stats(entries)
            return jsonify({"entries": entries, "stats": stats, "sort": sort_by, "desc": desc})
        return jsonify(get_sorted_library(sort_by, desc))

    @app.get("/api/library/export")
    def api_library_export() -> Any:
        return jsonify(export_library())

    @app.post("/api/library/import")
    def api_library_import() -> Any:
        payload = request.get_json(silent=True) or {}
        mode = request.args.get("mode", "merge").strip()
        # Accept both {"entries": [...]} and plain [...]
        if isinstance(payload, list):
            payload = {"entries": payload}
        result = import_library(payload, mode)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)

    @app.post("/api/library/bulk/remove")
    def api_library_bulk_remove() -> Any:
        payload = request.get_json(silent=True) or {}
        ids = payload.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids array is required"}), 400
        return jsonify(bulk_remove(ids))

    @app.post("/api/library/bulk/status")
    def api_library_bulk_status() -> Any:
        payload = request.get_json(silent=True) or {}
        ids = payload.get("ids", [])
        status = payload.get("status", "")
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids array is required"}), 400
        return jsonify(bulk_update_status(ids, status))

    @app.get("/api/library/<entry_id>")
    def api_library_get(entry_id: str) -> Any:
        entry = get_entry(entry_id)
        if entry:
            return jsonify(entry)
        return jsonify({"error": "Entry not found"}), 404

    @app.post("/api/library/add")
    def api_library_add() -> Any:
        payload = request.get_json(silent=True) or {}
        if not payload.get("title"):
            return jsonify({"error": "Title is required"}), 400
        result = add_entry(payload)
        if "error" in result:
            return jsonify(result), 409
        return jsonify(result)

    @app.post("/api/library/save")
    def api_library_add_or_update() -> Any:
        payload = request.get_json(silent=True) or {}
        if not payload.get("title"):
            return jsonify({"error": "Title is required"}), 400
        result = add_or_update_entry(payload)
        if "error" in result:
            return jsonify(result), 409
        return jsonify(result)

    @app.post("/api/library/update/<entry_id>")
    def api_library_update(entry_id: str) -> Any:
        payload = request.get_json(silent=True) or {}
        result = update_entry(entry_id, payload)
        if "error" in result:
            return jsonify(result), 404
        return jsonify(result)

    @app.post("/api/library/remove/<entry_id>")
    def api_library_remove(entry_id: str) -> Any:
        result = remove_entry(entry_id)
        if "error" in result:
            return jsonify(result), 404
        return jsonify(result)

    # ── Database: Anime ────────────────────────────────────────────────
    @app.get("/api/db/anime")
    def api_db_anime() -> Any:
        """Search anime across AniList, Jikan, Kitsu, AniDB"""
        q = request.args.get("q", "").strip()
        source = request.args.get("source", "anilist")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))

        if source == "anilist":
            return jsonify(_db_anilist_anime(q, page, per_page))
        elif source == "jikan":
            return jsonify(_db_jikan_anime(q, page))
        elif source == "kitsu":
            return jsonify(_db_kitsu_anime(q, page))
        elif source == "anidb":
            return jsonify(_db_anidb_search_anime(q, page))
        return jsonify({"error": "Invalid source"}), 400

    @app.get("/api/db/anime/<int:anime_id>")
    def api_db_anime_detail(anime_id: int) -> Any:
        """Get anime detail by ID (AniList, MAL, Kitsu, or AniDB)"""
        source = request.args.get("source", "anilist")
        if source == "anilist":
            return jsonify(_db_anilist_anime_by_id(anime_id))
        elif source == "jikan":
            return jsonify(_db_jikan_anime_by_id(anime_id))
        elif source == "kitsu":
            return jsonify(_db_kitsu_anime_by_id(str(anime_id)))
        elif source == "anidb":
            return jsonify(_db_anidb_anime_by_id(str(anime_id)))
        return jsonify({"error": "Invalid source"}), 400

    # ── Database: Manga ────────────────────────────────────────────────
    @app.get("/api/db/manga")
    def api_db_manga() -> Any:
        """Search manga across AniList, Kitsu"""
        q = request.args.get("q", "").strip()
        source = request.args.get("source", "anilist")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))

        if source == "anilist":
            return jsonify(_db_anilist_manga(q, page, per_page))
        elif source == "kitsu":
            return jsonify(_db_kitsu_manga(q, page))
        return jsonify({"error": "Invalid source"}), 400

    @app.get("/api/db/manga/<int:manga_id>")
    def api_db_manga_detail(manga_id: int) -> Any:
        """Get manga detail by ID"""
        source = request.args.get("source", "anilist")
        if source == "anilist":
            return jsonify(_db_anilist_manga_by_id(manga_id))
        elif source == "kitsu":
            return jsonify(_db_kitsu_manga_by_id(str(manga_id)))
        return jsonify({"error": "Invalid source"}), 400

    # ── Database: Studios ──────────────────────────────────────────────
    @app.get("/api/db/studios")
    def api_db_studios() -> Any:
        """Browse studios"""
        q = request.args.get("q", "").strip()
        source = request.args.get("source", "anilist")
        page = int(request.args.get("page", 1))

        if source == "anilist":
            return jsonify(_db_anilist_studios(q, page))
        return jsonify({"error": "Invalid source"}), 400

    @app.get("/api/db/studio/<int:studio_id>")
    def api_db_studio_detail(studio_id: int) -> Any:
        """Get studio detail and its anime"""
        source = request.args.get("source", "anilist")
        if source == "anilist":
            return jsonify(_db_anilist_studio_by_id(studio_id))
        return jsonify({"error": "Invalid source"}), 400

    # ── Database: Characters ───────────────────────────────────────────
    @app.get("/api/db/characters")
    def api_db_characters() -> Any:
        """Search characters"""
        q = request.args.get("q", "").strip()
        source = request.args.get("source", "anilist")
        page = int(request.args.get("page", 1))

        if source == "anilist":
            return jsonify(_db_anilist_characters(q, page))
        elif source == "kitsu":
            return jsonify(_db_kitsu_characters(q, page))
        return jsonify({"error": "Invalid source"}), 400

    @app.get("/api/db/character/<int:char_id>")
    def api_db_character_detail(char_id: int) -> Any:
        """Get character detail"""
        source = request.args.get("source", "anilist")
        if source == "anilist":
            return jsonify(_db_anilist_character_by_id(char_id))
        elif source == "kitsu":
            return jsonify(_db_kitsu_character_by_id(str(char_id)))
        return jsonify({"error": "Invalid source"}), 400

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    settings = load_settings()
    host = settings.web_host
    port = settings.web_port
    debug = settings.web_debug
    app.run(host=host, port=port, debug=debug, use_reloader=debug)


if __name__ == "__main__":
    main()
