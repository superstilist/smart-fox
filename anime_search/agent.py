from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from anime_search.config import Settings
from anime_search.models import UnifiedAnimeProfile
from anime_search.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    parse_description,
    web_search_anime,
    web_search_wikipedia,
    web_search_fandom,
)

log = logging.getLogger(__name__)

KB_PATH = Path(__file__).parent / "knowledge_base.json"


def _load_kb() -> list[dict[str, Any]]:
    if KB_PATH.exists():
        try:
            with open(KB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_kb(entries: list[dict[str, Any]]) -> None:
    with open(KB_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def _entry_matches(entry: dict[str, Any], query: str) -> bool:
    query_lower = query.lower()
    title = entry.get("title", "").lower()
    genres = " ".join(entry.get("genres", [])).lower()
    themes = " ".join(entry.get("themes", [])).lower()
    synopsis = entry.get("synopsis", "").lower()
    keywords = " ".join(entry.get("keywords", [])).lower()
    combined = f"{title} {genres} {themes} {synopsis} {keywords}"
    words = query_lower.split()
    return any(w in combined for w in words if len(w) > 2)


async def fetch_jikan_top_by_genre(genre: str, limit: int = 20) -> list[dict[str, Any]]:
    genre_map = {
        "action": 1, "adventure": 2, "comedy": 4, "drama": 8, "ecchi": 9,
        "fantasy": 10, "horror": 14, "mystery": 7, "romance": 22, "sci-fi": 24,
        "slice of life": 36, "sports": 30, "supernatural": 37, "thriller": 41,
        "mecha": 18, "isekai": 62, "school": 22, "military": 38,
        "psychological": 40, "music": 19, "historical": 13, "vampire": 32,
        "samurai": 21, "martial arts": 17, "harem": 35,
    }
    genre_id = genre_map.get(genre.lower())
    if not genre_id:
        return []
    url = f"https://api.jikan.moe/v4/anime"
    params = {"genres": genre_id, "limit": limit, "sfw": "true", "order_by": "score", "sort": "desc"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=3.0)) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(1.5)
                resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("Jikan fetch failed for genre %s: %s", genre, exc)
        return []
    entries = []
    for a in data.get("data", []):
        images = a.get("images", {}).get("jpg", {}) | a.get("images", {}).get("webp", {})
        entry = {
            "title": a.get("title", ""),
            "title_english": a.get("title_english", ""),
            "mal_id": a.get("mal_id"),
            "score": a.get("score"),
            "rank": a.get("rank"),
            "episodes": a.get("episodes"),
            "type": a.get("type"),
            "status": a.get("status"),
            "synopsis": (a.get("synopsis") or "")[:500],
            "genres": [g.get("name", "") for g in a.get("genres", [])],
            "themes": [t.get("name", "") for t in a.get("themes", [])],
            "studios": [s.get("name", "") for s in a.get("studios", [])],
            "poster": images.get("large_image_url") or images.get("image_url", ""),
            "url": a.get("url", ""),
            "keywords": [],
            "rating_score": a.get("score", 0) or 0,
            "popularity": a.get("popularity", 0) or 0,
            "source": "jikan",
            "fetched_at": time.time(),
        }
        entries.append(entry)
    return entries


async def auto_populate_kb() -> list[dict[str, Any]]:
    genres = ["action", "romance", "comedy", "fantasy", "isekai", "ecchi", "sci-fi", "sports", "horror", "slice of life", "drama", "supernatural", "mecha", "thriller", "school", "music", "vampire", "samurai", "harem", "psychological"]
    existing = _load_kb()
    existing_titles = {e.get("title", "").lower() for e in existing}
    new_entries = []
    for genre in genres:
        fetched = await fetch_jikan_top_by_genre(genre, limit=15)
        for entry in fetched:
            if entry["title"].lower() not in existing_titles:
                new_entries.append(entry)
                existing_titles.add(entry["title"].lower())
        await asyncio.sleep(0.5)
    all_entries = existing + new_entries
    _save_kb(all_entries)
    log.info("Knowledge base populated: %d existing + %d new = %d total", len(existing), len(new_entries), len(all_entries))
    return all_entries


SYSTEM_PROMPT = """\
You are an Anime & Manga Intelligent Information Agent.

Your job is to retrieve, combine, and summarize anime/manga information using ANY available tools.

You MUST NOT rely only on model memory.

---

## CORE BEHAVIOR

You act as a DATA AGGREGATION ENGINE, not a storyteller.

---

## MANDATORY FIRST STEP (ALREADY DONE)

The system has ALREADY searched DuckDuckGo, Wikipedia, and Fandom for your query.
The results are provided below in the user message as "PRE-SEARCHED WEB RESULTS".

You MUST analyze these web results FIRST before doing anything else.

---

## AFTER ANALYZING WEB RESULTS

You are FREE to use ANY tools you need:

- web_search_anime(query) — Search DuckDuckGo again for more specific info
- web_search_wikipedia(query) — Search Wikipedia for more details
- web_search_fandom(query) — Search Fandom for wiki lore
- search_anime_by_title(title) — Search MAL by exact title
- search_anime_by_genre(genre) — Search by genre
- search_anime_multi_api(query) — Search all APIs at once
- search_by_description_keywords(desc) — Parse description and search
- get_anime_details(title) — Get full details
- get_anime_recommendations(title) — Get recommendations
- semantic_search(query) — Semantic search
- hybrid_recommend(query) — Hybrid recommendation
- web_fetch_url(url) — Fetch a specific URL for more info

Use whatever tools give you the BEST results. You have full freedom.

---

## RESPONSE FORMAT

Always output structured result as JSON:

```json
{
  "engine": "agent",
  "source_title": "query or title",
  "top_50": [
    {
      "rank": 1,
      "title": "Anime Title",
      "type": "TV",
      "score": 8.5,
      "genres": ["Action", "Fantasy"],
      "status": "Finished Airing",
      "episodes": 24,
      "synopsis": "Short clean synopsis",
      "match_reason": "Why recommended",
      "rating": 85,
      "similarity_percentage": 84.9,
      "story_similarity": 80,
      "character_similarity": 75,
      "world_similarity": 70,
      "theme_similarity": 85,
      "power_system_similarity": 60,
      "emotional_similarity": 80,
      "art_style_similarity": 70,
      "music_similarity": 65,
      "pacing_similarity": 72,
      "tone_similarity": 78,
      "audience_similarity": 82,
      "genre_blend_similarity": 88,
      "overall_explanation": "Detailed explanation",
      "confidence_score": 90,
      "connection_type": "genre",
      "sources": ["duckduckgo", "wikipedia", "fandom", "jikan", "anilist"]
    }
  ]
}
```

---

## RATING SYSTEM (HARD)

You MUST rate each anime on 12 dimensions (0-100 each).

SIMILARITY SCALE:
- 99-90%: IDENTICAL
- 89-80%: VERY SIMILAR
- 79-70%: SIMILAR
- 69-60%: SOMEWHAT SIMILAR
- 59-50%: LIGHTLY SIMILAR
- Below 50%: NOT SIMILAR - Don't include

12 RATING DIMENSIONS:
1. story_similarity (15%)
2. character_similarity (12%)
3. world_similarity (10%)
4. theme_similarity (12%)
5. power_system_similarity (8%)
6. emotional_similarity (12%)
7. art_style_similarity (8%)
8. music_similarity (5%)
9. pacing_similarity (6%)
10. tone_similarity (5%)
11. audience_similarity (4%)
12. genre_blend_similarity (3%)

---

## STRICT RULES

- NEVER invent anime or manga
- NEVER hallucinate ratings or episodes
- If no data found → return empty top_50 with note
- Always prefer real retrieved data over model knowledge
- Be HARSH with ratings - most anime are NOT identical
- Only 99-90% for truly identical anime
- Most good matches are 70-80%, not 90%+

---

## FINAL GOAL

Combine web knowledge + API data + local database into the BEST possible recommendations.

Return ONLY the JSON response. No other text.
"""


class AnimeAgent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.tool_calls_log: list[dict[str, Any]] = []
        self.kb: list[dict[str, Any]] = _load_kb()

    async def research(
        self,
        query: str,
        user_description: str = "",
        profile: UnifiedAnimeProfile | None = None,
        on_tool_call: Any = None,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        if len(self.kb) < 50:
            log.info("Knowledge base too small (%d entries), auto-populating...", len(self.kb))
            self.kb = await auto_populate_kb()

        if on_progress:
            await on_progress(0, [], "Pre-searching DuckDuckGo, Wikipedia, Fandom...")

        web_results = await self._pre_search_web(query, user_description)

        if on_tool_call:
            await on_tool_call("web_search_anime", {"query": query}, "done", web_results.get("duckduckgo"))
            await on_tool_call("web_search_wikipedia", {"query": query}, "done", web_results.get("wikipedia"))
            await on_tool_call("web_search_fandom", {"query": query}, "done", web_results.get("fandom"))

        base_url = self.settings.effective_ai_base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"
        headers: dict[str, str] = {}
        if self.settings.local_ai_api_key and self.settings.local_ai_api_key != "local-key":
            headers["Authorization"] = f"Bearer {self.settings.local_ai_api_key}"

        user_message = self._build_user_message(query, user_description, profile, web_results)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        self.tool_calls_log = []
        accumulated_text = ""
        total_tool_calls = 0

        for iteration in range(self.settings.agent_max_iterations):
            if on_progress:
                await on_progress(iteration, self.tool_calls_log, accumulated_text)

            payload = {
                "model": self.settings.effective_ai_model,
                "messages": messages,
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
                "temperature": self.settings.ai_temperature,
                "max_tokens": self.settings.ai_max_tokens,
            }

            try:
                async with httpx.AsyncClient(timeout=self.settings.ai_http_timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    response_data = response.json()
            except Exception as exc:
                log.warning("Agent LLM call failed at iteration %d: %s", iteration, exc)
                break

            choice = response_data.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content", "") or ""
            tool_calls = message.get("tool_calls") or []

            if content:
                accumulated_text += content

            if not tool_calls:
                if content:
                    result = self._parse_final_response(content, accumulated_text)
                    if result.get("top_50"):
                        return result
                break

            if total_tool_calls >= self.settings.agent_max_tool_calls:
                log.warning("Agent hit tool call limit (%d)", self.settings.agent_max_tool_calls)
                break

            messages.append(message)

            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                if on_tool_call:
                    await on_tool_call(tool_name, args, "running")

                result = await self.execute_kb_tool(tool_name, args)
                total_tool_calls += 1
                self.tool_calls_log.append({
                    "tool": tool_name,
                    "arguments": args,
                    "result": result,
                    "timestamp": time.time(),
                })

                if on_tool_call:
                    await on_tool_call(tool_name, args, "done", result)

                tool_result_text = json.dumps(result, ensure_ascii=False)
                if len(tool_result_text) > 2000:
                    tool_result_text = tool_result_text[:2000] + "..."

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": tool_result_text,
                })

                await asyncio.sleep(self.settings.agent_tool_delay)

        return self._parse_final_response(accumulated_text, accumulated_text)

    async def execute_kb_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "search_knowledge_base":
            query = args.get("query", "")
            results = [e for e in self.kb if _entry_matches(e, query)]
            results.sort(key=lambda x: x.get("score", 0) or 0, reverse=True)
            return {"results": results[:20], "total": len(results)}

        if name == "add_to_knowledge_base":
            title = args.get("title", "")
            if not title:
                return {"error": "Title is required"}
            existing = [e for e in self.kb if e.get("title", "").lower() == title.lower()]
            if existing:
                return {"error": f"'{title}' already exists in knowledge base"}
            entry = {
                "title": title,
                "title_english": args.get("title_english", ""),
                "mal_id": args.get("mal_id"),
                "score": args.get("score", 0),
                "rank": args.get("rank"),
                "episodes": args.get("episodes"),
                "type": args.get("type", "TV"),
                "status": args.get("status", "Finished Airing"),
                "synopsis": args.get("synopsis", ""),
                "genres": args.get("genres", []),
                "themes": args.get("themes", []),
                "studios": args.get("studios", []),
                "poster": args.get("poster", ""),
                "url": args.get("url", ""),
                "keywords": args.get("keywords", []),
                "rating_score": args.get("score", 0),
                "popularity": args.get("popularity", 0),
                "source": "manual",
                "fetched_at": time.time(),
            }
            self.kb.append(entry)
            _save_kb(self.kb)
            return {"success": True, "message": f"Added '{title}' to knowledge base", "total": len(self.kb)}

        if name == "edit_knowledge_base":
            title = args.get("title", "")
            fields = args.get("fields", {})
            if not title or not fields:
                return {"error": "Title and fields are required"}
            for entry in self.kb:
                if entry.get("title", "").lower() == title.lower():
                    for key, value in fields.items():
                        if key in entry:
                            entry[key] = value
                    entry["fetched_at"] = time.time()
                    _save_kb(self.kb)
                    return {"success": True, "message": f"Updated '{title}' in knowledge base"}
            return {"error": f"'{title}' not found in knowledge base"}

        if name == "delete_from_knowledge_base":
            title = args.get("title", "")
            if not title:
                return {"error": "Title is required"}
            original_count = len(self.kb)
            self.kb = [e for e in self.kb if e.get("title", "").lower() != title.lower()]
            if len(self.kb) < original_count:
                _save_kb(self.kb)
                return {"success": True, "message": f"Deleted '{title}' from knowledge base", "total": len(self.kb)}
            return {"error": f"'{title}' not found in knowledge base"}

        if name == "fetch_anime_from_internet":
            genre = args.get("genre", "")
            limit = args.get("limit", 20)
            if not genre:
                return {"error": "Genre is required"}
            fetched = await fetch_jikan_top_by_genre(genre, limit)
            existing_titles = {e.get("title", "").lower() for e in self.kb}
            new_count = 0
            for entry in fetched:
                if entry["title"].lower() not in existing_titles:
                    self.kb.append(entry)
                    existing_titles.add(entry["title"].lower())
                    new_count += 1
            _save_kb(self.kb)
            return {"success": True, "fetched": len(fetched), "added": new_count, "total": len(self.kb)}

        if name == "web_search_anime":
            query = args.get("query", "")
            if not query:
                return {"error": "Query is required"}
            return await web_search_anime(query)

        if name == "web_search_wikipedia":
            query = args.get("query", "")
            if not query:
                return {"error": "Query is required"}
            return await web_search_wikipedia(query)

        if name == "web_search_fandom":
            query = args.get("query", "")
            if not query:
                return {"error": "Query is required"}
            return await web_search_fandom(query)

        return await execute_tool(name, args)

    async def _pre_search_web(self, query: str, user_description: str = "") -> dict[str, Any]:
        search_query = user_description[:200] if user_description else query
        ddg_task = web_search_anime(search_query)
        wiki_task = web_search_wikipedia(search_query)
        fandom_task = web_search_fandom(search_query)

        ddg_result, wiki_result, fandom_result = await asyncio.gather(
            ddg_task, wiki_task, fandom_task, return_exceptions=True
        )

        ddg_results = ddg_result.get("results", []) if isinstance(ddg_result, dict) else []
        wiki_results = wiki_result.get("results", []) if isinstance(wiki_result, dict) else []
        fandom_results = fandom_result.get("results", []) if isinstance(fandom_result, dict) else []

        return {
            "duckduckgo": ddg_results,
            "wikipedia": wiki_results,
            "fandom": fandom_results,
        }

    def _build_user_message(self, query: str, user_description: str = "", profile: UnifiedAnimeProfile | None = None, web_results: dict[str, Any] | None = None) -> str:
        web_section = ""
        if web_results:
            web_section = "\n\nPRE-SEARCHED WEB RESULTS (DuckDuckGo + Wikipedia + Fandom):\n"

            ddg = web_results.get("duckduckgo", [])
            if ddg:
                web_section += "\n--- DuckDuckGo Results ---\n"
                for i, r in enumerate(ddg[:8], 1):
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")
                    url = r.get("url", "")
                    web_section += f"{i}. {title}\n   {snippet}\n   {url}\n"

            wiki = web_results.get("wikipedia", [])
            if wiki:
                web_section += "\n--- Wikipedia Results ---\n"
                for i, r in enumerate(wiki[:5], 1):
                    title = r.get("title", "")
                    extract = r.get("extract", "")[:300]
                    url = r.get("url", "")
                    web_section += f"{i}. {title}\n   {extract}\n   {url}\n"

            fandom = web_results.get("fandom", [])
            if fandom:
                web_section += "\n--- Fandom Results ---\n"
                for i, r in enumerate(fandom[:5], 1):
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")
                    url = r.get("url", "")
                    web_section += f"{i}. {title}\n   {snippet}\n   {url}\n"

        kb_sample = self.kb[:50]
        kb_text = ""
        if kb_sample:
            kb_text = "\n\nLOCAL DATABASE SAMPLE (use search_knowledge_base for full search):\n"
            for i, item in enumerate(kb_sample[:20], 1):
                genres = ", ".join(item.get("genres", [])[:3])
                kb_text += f"{i}. {item.get('title', 'Unknown')} [{genres}] - Score: {item.get('score', '?')}\n"

        profile_context = ""
        if profile:
            titles = profile.titles.get("all", []) or profile.titles.get("english", []) or profile.titles.get("romaji", [])
            genres = ", ".join(profile.genres[:8]) if profile.genres else "None"
            themes = ", ".join(profile.themes[:8]) if profile.themes else "None"
            studios = ", ".join(profile.studios[:5]) if profile.studios else "None"
            char_names = [c.get("name", "") for c in profile.characters[:10] if c.get("name")]
            characters = ", ".join(char_names) if char_names else "None"
            rec_titles = [r.get("title", "") for r in profile.recommendations[:15] if r.get("title")]
            recs = ", ".join(rec_titles) if rec_titles else "None"
            desc_text = (profile.description.get("summary", "") or "")[:500]

            profile_context = (
                f"\n\nFETCHED DATA (from AniList/Jikan/Kitsu):\n"
                f"- Title: {', '.join(titles[:3]) if titles else query}\n"
                f"- Genres: {genres}\n"
                f"- Themes: {themes}\n"
                f"- Studios: {studios}\n"
                f"- Characters: {characters}\n"
                f"- Provider recommendations: {recs}\n"
                f"- Description: {desc_text}\n"
            )

        parsed_info = ""
        if user_description:
            parsed = parse_description(user_description)
            parsed_info = (
                f"\n\nDESCRIPTION ANALYSIS:\n"
                f"- Matched Genres: {', '.join(parsed.get('genres', [])) or 'None'}\n"
                f"- Matched Tones: {', '.join(parsed.get('tones', [])) or 'None'}\n"
                f"- Matched Settings: {', '.join(parsed.get('settings', [])) or 'None'}\n"
                f"- Matched Character Types: {', '.join(parsed.get('character_types', [])) or 'None'}\n"
            )

        if user_description:
            return (
                f"TASK: Find anime matching this description:\n"
                f'"{user_description}"\n'
                f"{parsed_info}"
                f"{web_section}"
                f"{kb_text}"
                f"{profile_context}\n"
                f"INSTRUCTIONS: Analyze the pre-searched web results above. Use any additional tools you need. Return structured JSON with top_50 array."
            )
        else:
            return (
                f"TASK: Find anime similar to: {query}\n"
                f"{web_section}"
                f"{kb_text}"
                f"{profile_context}\n"
                f"INSTRUCTIONS: Analyze the pre-searched web results above. Use any additional tools you need. Return structured JSON with top_50 array."
            )

    def _parse_final_response(self, text: str, accumulated: str) -> dict[str, Any]:
        for candidate_text in [text, accumulated]:
            result = self._try_parse_json(candidate_text)
            if result and "top_50" in result:
                return result

        json_match = re.search(r"```json\s*(.*?)\s*```", accumulated, re.DOTALL)
        if json_match:
            result = self._try_parse_json(json_match.group(1))
            if result and "top_50" in result:
                return result

        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", accumulated, re.DOTALL)
        if brace_match:
            result = self._try_parse_json(brace_match.group(0))
            if result and "top_50" in result:
                return result

        return {
            "engine": "agent",
            "source_title": "Agent research",
            "top_50": [],
            "tool_calls": self.tool_calls_log,
            "raw_text": accumulated,
        }

    def _try_parse_json(self, text: str) -> dict[str, Any] | None:
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        cleaned = re.sub(r"```(?:json)?\s*", "", text)
        cleaned = re.sub(r"```", "", cleaned).strip()
        try:
            result = json.loads(cleaned)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        return None
