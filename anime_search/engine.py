from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from typing import Any

import httpx

from anime_search.ai import (
    recommend_with_local_ai,
    recommend_with_local_ai_streaming,
    parse_streaming_chunks,
    agent_recommend,
)
from anime_search.cache import SQLiteJsonCache
from anime_search.config import Settings
from anime_search.image_cache import cache_profile_images
from anime_search.merge import merge_profiles
from anime_search.models import SourceResult, UnifiedAnimeProfile, SearchType
from anime_search.providers import AniListProvider, JikanProvider, KitsuProvider
from anime_search.recommender import fallback_recommendations, normalize_ai_recommendations

log = logging.getLogger(__name__)

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()

GENRE_KEYWORDS = {"action", "adventure", "comedy", "drama", "fantasy", "horror", "mystery", "romance", "sci-fi", "slice of life", "sports", "supernatural", "thriller", "mecha", "isekai", "ecchi", "harem", "school", "military", "psychological", "music", "historical", "vampire", "samurai", "martial arts"}
THEME_KEYWORDS = {"isekai", "school", "military", "music", "vampire", "samurai", "post-apocalyptic", "cyberpunk", "steampunk", "dystopia", "utopia"}


def classify_search_type(query: str, description: str) -> SearchType:
    if description and not query:
        return SearchType.DESCRIPTION
    combined = f"{query} {description}".lower()
    if any(kw in combined for kw in GENRE_KEYWORDS):
        if any(kw in combined for kw in THEME_KEYWORDS):
            return SearchType.THEME
        return SearchType.GENRE
    if len(query.split()) <= 3 and not any(c in query for c in "?!"):
        return SearchType.TITLE
    return SearchType.DESCRIPTION


def _get_task(task_id: str) -> dict[str, Any] | None:
    with _tasks_lock:
        return _tasks.get(task_id)


def _set_task(task_id: str, data: dict[str, Any]) -> None:
    with _tasks_lock:
        _tasks[task_id] = data


def _update_task(task_id: str, **kwargs: Any) -> None:
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)


def cleanup_old_tasks() -> None:
    now = time.time()
    with _tasks_lock:
        expired = [k for k, v in _tasks.items() if now - v.get("created_at", 0) > 600]
        for k in expired:
            del _tasks[k]


class AnimeSearchEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.cache = SQLiteJsonCache(self.settings.cache_path, self.settings.cache_ttl_seconds)

    async def search(
        self,
        query: str,
        description: str = "",
        content_filter: str = "sfw",
        negative_prompt: str = "",
    ) -> UnifiedAnimeProfile:
        use_cache = not description
        cache_key = query.strip().lower()

        if use_cache:
            cached = self.cache.get("merged", cache_key)
            if cached is not None:
                log.debug("Cache hit for search: %s", cache_key)
                return UnifiedAnimeProfile.model_validate(cached)

        async with httpx.AsyncClient(timeout=self.settings.http_timeout) as client:
            providers = [
                AniListProvider(client, self.cache, self.settings),
                JikanProvider(client, self.cache, self.settings),
                KitsuProvider(client, self.cache, self.settings),
            ]

            results: list[SourceResult] = []
            for provider in providers:
                result = await provider.search(
                    cache_key,
                    content_filter=content_filter,
                    negative_prompt=negative_prompt,
                )
                results.append(result)
                status = "OK" if result.ok else f"FAILED ({result.error})"
                log.info("Provider %s: %s", provider.name, status)

            successful_count = sum(1 for r in results if r.ok)
            if successful_count == 0:
                errors = "; ".join(f"{r.source}: {r.error}" for r in results)
                raise RuntimeError(f"All anime data providers failed: {errors}")

            log.info("Merging %d/%d successful provider results for '%s'", successful_count, len(results), cache_key)
            profile = merge_profiles(cache_key, results)
            if description:
                existing_desc = profile.description.get("summary", "") or ""
                profile.description["summary"] = f"{existing_desc}\n\nUser context: {description}" if existing_desc else description
            try:
                await cache_profile_images(client, self.cache, profile, self.settings.cache_path)
            except Exception:
                log.warning("Image caching failed, continuing without cached images.")

        if use_cache:
            self.cache.set("merged", cache_key, profile.model_dump(mode="json"))
        return profile

    async def recommend(
        self,
        query: str,
        user_description: str = "",
        content_filter: str = "sfw",
        negative_prompt: str = "",
    ) -> dict:
        profile = await self.search(query, user_description, content_filter, negative_prompt)
        try:
            ai_result = await agent_recommend(profile, self.settings, user_description)
            recommendation = normalize_ai_recommendations(ai_result, profile)
            recommendation["engine"] = "agent"
            recommendation["tool_calls"] = ai_result.get("tool_calls", [])
            if not recommendation.get("top_50"):
                log.warning("Agent returned empty top_50, using fallback")
                recommendation = fallback_recommendations(profile)
                recommendation["engine"] = "jikan-fallback"
                recommendation["ai_error"] = "Agent returned empty results"
        except Exception as exc:
            log.warning("Agent recommendation failed: %s", exc)
            recommendation = fallback_recommendations(profile)
            recommendation["ai_error"] = str(exc)
        recommendation["source_title"] = profile.get_primary_title()
        return recommendation

    def start_background_recommend(
        self,
        query: str,
        user_description: str = "",
        content_filter: str = "sfw",
        negative_prompt: str = "",
    ) -> str:
        task_id = str(uuid.uuid4())
        _set_task(task_id, {
            "status": "starting",
            "progress": 0,
            "message": "Initializing...",
            "results": [],
            "error": None,
            "created_at": time.time(),
            "query": query,
            "user_description": user_description,
            "content_filter": content_filter,
            "negative_prompt": negative_prompt,
            "profile": None,
            "recommendation": None,
            "tool_calls": [],
        })

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._background_task(
                    task_id, query, user_description, content_filter, negative_prompt,
                ))
            except Exception as exc:
                _update_task(task_id, status="error", error=str(exc), progress=100)
            finally:
                loop.close()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return task_id

    async def _background_task(
        self,
        task_id: str,
        query: str,
        user_description: str,
        content_filter: str = "sfw",
        negative_prompt: str = "",
    ) -> None:
        log.info("Background task %s started for query: %s (description: %s)", task_id, query, bool(user_description))
        _update_task(task_id, status="searching", progress=10, message="Searching anime databases...")
        try:
            profile = await self.search(query, user_description, content_filter, negative_prompt)
            log.info("Background task %s: profile loaded, providers: %s", task_id, list(profile.provider_status.keys()))
        except Exception as exc:
            log.error("Background task %s: search failed: %s", task_id, exc)
            _update_task(task_id, status="error", error=str(exc), progress=100)
            return
        _update_task(task_id, progress=25, message="Profile loaded. Starting AI agent...", profile=profile.model_dump(mode="json"))

        ok_providers = [name for name, status in profile.provider_status.items() if status.get("ok")]
        log.info("Background task %s: ok_providers=%s", task_id, ok_providers)
        if not ok_providers:
            rec = fallback_recommendations(profile)
            rec["source_title"] = profile.get_primary_title()
            _update_task(task_id, status="done", progress=100, recommendation=rec, results=rec.get("top_50", []), message="Done (fallback - no providers).")
            return

        _update_task(task_id, progress=30, message="Agent researching...")

        async def on_tool_call(tool_name: str, args: dict, status: str, result: Any = None) -> None:
            task_data = _get_task(task_id) or {}
            calls = task_data.get("tool_calls", [])
            calls.append({"tool": tool_name, "arguments": args, "status": status, "result": result})
            _update_task(task_id, tool_calls=calls)
            if status == "running":
                _update_task(task_id, progress=min(85, 30 + len(calls) * 4), message=f"Agent using tool: {tool_name}...")

        async def on_progress(iteration: int, calls: list, text: str) -> None:
            _update_task(task_id, progress=min(85, 25 + iteration * 4), message=f"Agent researching... (step {iteration + 1})")

        try:
            log.info("Background task %s: starting agent recommend", task_id)
            ai_result = await agent_recommend(profile, self.settings, user_description, on_tool_call=on_tool_call, on_progress=on_progress)
            log.info("Background task %s: agent returned, top_50 count=%d", task_id, len(ai_result.get("top_50", [])))
            recommendation = normalize_ai_recommendations(ai_result, profile)
            recommendation["engine"] = "agent"
            recommendation["tool_calls"] = ai_result.get("tool_calls", [])
            recommendation["ai_raw_text"] = ai_result.get("raw_text", "")
            if not recommendation.get("top_50"):
                log.warning("Background task %s: agent returned empty top_50, using fallback", task_id)
                recommendation = fallback_recommendations(profile)
                recommendation["engine"] = "jikan-fallback"
                recommendation["ai_error"] = "Agent returned empty results"
        except Exception as exc:
            log.warning("Background task %s: agent failed: %s", task_id, exc)
            recommendation = fallback_recommendations(profile)
            recommendation["ai_error"] = str(exc)

        recommendation["source_title"] = profile.get_primary_title()
        _update_task(task_id, status="done", progress=100, recommendation=recommendation, results=recommendation.get("top_50", []), message="Agent research complete!")

    def start_background_agent_recommend(
        self,
        query: str,
        user_description: str = "",
        content_filter: str = "sfw",
        negative_prompt: str = "",
    ) -> str:
        return self.start_background_recommend(query, user_description, content_filter, negative_prompt)
