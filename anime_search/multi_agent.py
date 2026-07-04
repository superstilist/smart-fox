from __future__ import annotations

import asyncio
import json
import logging
import time
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
from anime_search.anime_index import get_anime_index

log = logging.getLogger(__name__)


EXPLORER_SYSTEM_PROMPT = """\
You are the EXPLORER agent in an AI anime recommendation system.

Your job is to SEARCH and GATHER information about anime. You are a data collector.

## YOUR ROLE
- Search for anime using ALL available tools
- Gather comprehensive data from multiple sources
- Build a rich dataset for the Advocate agent to evaluate
- Focus on FINDING anime, not judging them

## AVAILABLE TOOLS
- web_search_anime(query) — Search DuckDuckGo
- web_search_wikipedia(query) — Search Wikipedia
- web_search_fandom(query) — Search Fandom
- search_anime_by_title(title) — Search MAL
- search_anime_by_genre(genre) — Search by genre
- search_anime_multi_api(query) — Search all APIs
- get_anime_recommendations(title) — Get similar anime
- search_by_description_keywords(desc) — Parse description
- semantic_search(query) — Semantic search
- hybrid_recommend(query) — Hybrid recommendation

## STRATEGY
1. Start with web searches (DuckDuckGo, Wikipedia, Fandom)
2. Use API searches for detailed data
3. Get recommendations from found anime
4. Search by genre/theme if description-based
5. Collect as many relevant candidates as possible

## OUTPUT FORMAT
Return a JSON object with ALL candidates you found:
```json
{
  "agent": "explorer",
  "candidates": [
    {
      "title": "Anime Title",
      "score": 8.5,
      "genres": ["Action", "Fantasy"],
      "themes": ["Isekai"],
      "synopsis": "Short synopsis",
      "source": "jikan|anilist|web",
      "confidence": 0.9
    }
  ],
  "search_summary": "What you searched and found",
  "total_found": 42
}
```

Be THOROUGH. Find as many relevant anime as possible.
"""


ADVOCATE_SYSTEM_PROMPT = """\
You are the ADVOCATE agent in an AI anime recommendation system.

Your job is to EVALUATE and SCORE anime candidates based on user preferences.

## YOUR ROLE
- Receive candidates from the Explorer agent
- Evaluate each candidate against the user's query/description
- Score each candidate on 12 similarity dimensions
- Select the BEST matches
- Provide detailed reasoning for your choices

## SCORING DIMENSIONS (0-100 each)
1. story_similarity (15%) — How similar is the plot/story?
2. character_similarity (12%) — How similar are the characters?
3. world_similarity (10%) — How similar is the world/setting?
4. theme_similarity (12%) — How similar are the themes?
5. power_system_similarity (8%) — How similar is the power system?
6. emotional_similarity (12%) — How similar is the emotional tone?
7. art_style_similarity (8%) — How similar is the art style?
8. music_similarity (5%) — How similar is the music?
9. pacing_similarity (6%) — How similar is the pacing?
10. tone_similarity (5%) — How similar is the overall tone?
11. audience_similarity (4%) — How similar is the target audience?
12. genre_blend_similarity (3%) — How similar is the genre blend?

## SIMILARITY SCALE
- 90-100%: Near-identical match
- 80-89%: Very strong match
- 70-79%: Strong match
- 60-69%: Good match
- 50-59%: Moderate match
- Below 50%: Skip (don't include)

## OUTPUT FORMAT
Return a JSON object with your top picks:
```json
{
  "agent": "advocate",
  "top_picks": [
    {
      "rank": 1,
      "title": "Anime Title",
      "score": 8.5,
      "genres": ["Action", "Fantasy"],
      "synopsis": "Why this matches",
      "match_reason": "Detailed explanation",
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
      "confidence_score": 90
    }
  ],
  "evaluated_count": 42,
  "selected_count": 10
}
```

Be HARSH with ratings. Most anime are NOT identical.
Only include anime that truly match the user's request.
"""


REVIEWER_SYSTEM_PROMPT = """\
You are the REVIEWER agent in an AI anime recommendation system.

Your job is to FINALIZE and RANK the top anime recommendations.

## YOUR ROLE
- Receive top picks from the Advocate agent
- Do a final quality check
- Re-rank based on overall quality and diversity
- Ensure no duplicates or bad entries
- Produce the final recommendation list

## QUALITY CHECKS
1. Remove any anime with score < 50%
2. Remove duplicates (same anime, different titles)
3. Ensure genre diversity (don't have all same genre)
4. Verify synopses are accurate
5. Check that recommendations make sense together

## RANKING CRITERIA
1. Overall similarity score (primary)
2. Popularity and acclaim (secondary)
3. Genre diversity (tertiary)
4. Recency (slight boost for newer anime)

## OUTPUT FORMAT
Return the final JSON response:
```json
{
  "agent": "reviewer",
  "engine": "multi-agent",
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
      "sources": ["explorer", "advocate"]
    }
  ],
  "commentary": ["line 1", "line 2", ...]
}
```

Return ONLY the JSON response. No other text.
"""


class ExplorerAgent:
    """Searches and gathers anime candidates from multiple sources."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def search(
        self,
        query: str,
        user_description: str = "",
        profile: UnifiedAnimeProfile | None = None,
        on_commentary: Any = None,
    ) -> dict[str, Any]:
        candidates = []
        commentary = []

        if on_commentary:
            await on_commentary("## Explorer: Starting comprehensive search...")

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

        if on_commentary:
            await on_commentary(f"## Explorer: Found {len(ddg_results)} DuckDuckGo, {len(wiki_results)} Wikipedia, {len(fandom_results)} Fandom results")

        from anime_search.tools import search_anime_by_title, search_anime_by_genre, search_anime_multi_api

        api_results = await search_anime_multi_api(search_query, limit=5)
        for item in api_results:
            candidates.append({
                "title": item.get("title", ""),
                "score": item.get("score"),
                "genres": item.get("genres", []),
                "themes": item.get("themes", []),
                "synopsis": item.get("synopsis", ""),
                "source": item.get("source", "api"),
                "confidence": 0.8,
            })

        if on_commentary:
            await on_commentary(f"## Explorer: Found {len(api_results)} anime from APIs")

        parsed = parse_description(search_query)
        if parsed.get("genres"):
            for genre in parsed["genres"][:3]:
                genre_results = await search_anime_by_genre(genre, limit=5)
                for item in genre_results:
                    candidates.append({
                        "title": item.get("title", ""),
                        "score": item.get("score"),
                        "genres": item.get("genres", []),
                        "themes": item.get("themes", []),
                        "synopsis": item.get("synopsis", ""),
                        "source": "genre_search",
                        "confidence": 0.7,
                    })
                await asyncio.sleep(0.5)

        if profile:
            rec_titles = [r.get("title", "") for r in profile.recommendations[:10] if r.get("title")]
            for title in rec_titles:
                rec_results = await search_anime_by_title(title, limit=1)
                for item in rec_results:
                    candidates.append({
                        "title": item.get("title", ""),
                        "score": item.get("score"),
                        "genres": item.get("genres", []),
                        "themes": item.get("themes", []),
                        "synopsis": item.get("synopsis", ""),
                        "source": "provider_rec",
                        "confidence": 0.85,
                    })
                await asyncio.sleep(0.3)

        seen = set()
        unique_candidates = []
        for c in candidates:
            key = c.get("title", "").lower().strip()
            if key and key not in seen and len(key) > 2:
                seen.add(key)
                unique_candidates.append(c)

        return {
            "agent": "explorer",
            "candidates": unique_candidates,
            "search_summary": f"Searched DuckDuckGo, Wikipedia, Fandom, Jikan, AniList. Found {len(unique_candidates)} unique candidates.",
            "total_found": len(unique_candidates),
        }


class AdvocateAgent:
    """Evaluates and scores anime candidates."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def evaluate(
        self,
        candidates: list[dict[str, Any]],
        query: str,
        user_description: str = "",
        on_commentary: Any = None,
    ) -> dict[str, Any]:
        if on_commentary:
            await on_commentary(f"## Advocate: Evaluating {len(candidates)} candidates...")

        base_url = self.settings.effective_ai_base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"
        headers: dict[str, str] = {}
        if self.settings.local_ai_api_key and self.settings.local_ai_api_key != "local-key":
            headers["Authorization"] = f"Bearer {self.settings.local_ai_api_key}"

        candidates_text = json.dumps(candidates[:30], ensure_ascii=False)
        user_message = (
            f"Evaluate these anime candidates for the query: {query}\n\n"
            f"User description: {user_description or 'None'}\n\n"
            f"Candidates:\n{candidates_text}\n\n"
            f"Score each on 12 dimensions and return top picks as JSON."
        )

        messages = [
            {"role": "system", "content": ADVOCATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        payload = {
            "model": self.settings.effective_ai_model,
            "messages": messages,
            "temperature": self.settings.ai_temperature,
            "max_tokens": self.settings.ai_max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_http_timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                response_data = response.json()

            content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                result = self._parse_response(content)
                if result and result.get("top_picks"):
                    if on_commentary:
                        await on_commentary(f"## Advocate: Selected {len(result['top_picks'])} top picks")
                    return result
        except Exception as exc:
            log.warning("Advocate LLM call failed: %s", exc)
            if on_commentary:
                await on_commentary(f"## Advocate: LLM failed, using algorithmic scoring")

        return self._algorithmic_evaluate(candidates, query, user_description)

    def _algorithmic_evaluate(
        self,
        candidates: list[dict[str, Any]],
        query: str,
        user_description: str,
    ) -> dict[str, Any]:
        scored = []
        query_lower = query.lower()
        desc_lower = user_description.lower() if user_description else ""

        for c in candidates:
            score = 50
            title = c.get("title", "").lower()
            genres = [g.lower() for g in c.get("genres", [])]
            synopsis = c.get("synopsis", "").lower()

            if any(w in title for w in query_lower.split() if len(w) > 2):
                score += 20
            if any(w in synopsis for w in query_lower.split() if len(w) > 2):
                score += 15
            if desc_lower:
                desc_words = [w for w in desc_lower.split() if len(w) > 3]
                if any(w in synopsis for w in desc_words):
                    score += 25
                if any(w in " ".join(genres) for w in desc_words):
                    score += 15

            mal_score = c.get("score") or 0
            if mal_score >= 8:
                score += 10
            elif mal_score >= 7:
                score += 5

            score = min(100, max(0, score))

            if score >= 50:
                scored.append({
                    "rank": 0,
                    "title": c.get("title", ""),
                    "score": c.get("score"),
                    "genres": c.get("genres", []),
                    "synopsis": c.get("synopsis", ""),
                    "match_reason": f"Matched on keywords and genre",
                    "rating": score,
                    "similarity_percentage": score,
                    "story_similarity": score,
                    "character_similarity": score - 5,
                    "world_similarity": score - 10,
                    "theme_similarity": score,
                    "power_system_similarity": score - 15,
                    "emotional_similarity": score - 5,
                    "art_style_similarity": score - 10,
                    "music_similarity": score - 15,
                    "pacing_similarity": score - 10,
                    "tone_similarity": score - 5,
                    "audience_similarity": score - 10,
                    "genre_blend_similarity": score,
                    "overall_explanation": f"Matches query based on keywords and genre analysis",
                    "confidence_score": score,
                })

        scored.sort(key=lambda x: x.get("rating", 0), reverse=True)

        for i, item in enumerate(scored[:20], 1):
            item["rank"] = i

        return {
            "agent": "advocate",
            "top_picks": scored[:20],
            "evaluated_count": len(candidates),
            "selected_count": len(scored[:20]),
        }

    def _parse_response(self, text: str) -> dict[str, Any] | None:
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        import re
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(1))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if brace_match:
            try:
                result = json.loads(brace_match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        return None


class ReviewerAgent:
    """Finalizes and ranks the top recommendations."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def review(
        self,
        top_picks: list[dict[str, Any]],
        query: str,
        user_description: str = "",
        commentary_log: list[str] | None = None,
        on_commentary: Any = None,
    ) -> dict[str, Any]:
        if on_commentary:
            await on_commentary(f"## Reviewer: Reviewing {len(top_picks)} picks...")

        base_url = self.settings.effective_ai_base_url.rstrip("/")
        url = f"{base_url}/v1/chat/completions"
        headers: dict[str, str] = {}
        if self.settings.local_ai_api_key and self.settings.local_ai_api_key != "local-key":
            headers["Authorization"] = f"Bearer {self.settings.local_ai_api_key}"

        picks_text = json.dumps(top_picks[:15], ensure_ascii=False)
        user_message = (
            f"Review and finalize these anime recommendations for: {query}\n\n"
            f"User description: {user_description or 'None'}\n\n"
            f"Top picks:\n{picks_text}\n\n"
            f"Do quality checks, re-rank, and return final top_50 as JSON."
        )

        messages = [
            {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        if commentary_log:
            messages.append({
                "role": "assistant",
                "content": "Previous commentary:\n" + "\n".join(commentary_log[-20:]),
            })

        payload = {
            "model": self.settings.effective_ai_model,
            "messages": messages,
            "temperature": self.settings.ai_temperature,
            "max_tokens": self.settings.ai_max_tokens,
        }

        try:
            async with httpx.AsyncClient(timeout=self.settings.ai_http_timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                response_data = response.json()

            content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                result = self._parse_response(content)
                if result and result.get("top_50"):
                    if on_commentary:
                        await on_commentary(f"## Reviewer: Finalized {len(result['top_50'])} recommendations")
                    return result
        except Exception as exc:
            log.warning("Reviewer LLM call failed: %s", exc)
            if on_commentary:
                await on_commentary(f"## Reviewer: LLM failed, using algorithmic finalization")

        return self._algorithmic_review(top_picks, query)

    def _algorithmic_review(
        self,
        top_picks: list[dict[str, Any]],
        query: str,
    ) -> dict[str, Any]:
        seen = set()
        unique = []
        for item in top_picks:
            title = item.get("title", "").lower().strip()
            if title and title not in seen:
                seen.add(title)
                unique.append(item)

        unique.sort(key=lambda x: x.get("rating", 0), reverse=True)

        for i, item in enumerate(unique[:50], 1):
            item["rank"] = i

        return {
            "agent": "reviewer",
            "engine": "multi-agent",
            "top_50": unique[:50],
            "commentary": [
                f"Reviewed {len(top_picks)} candidates",
                f"Removed {len(top_picks) - len(unique)} duplicates",
                f"Final list: {len(unique[:50])} recommendations",
            ],
        }

    def _parse_response(self, text: str) -> dict[str, Any] | None:
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        import re
        json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(1))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if brace_match:
            try:
                result = json.loads(brace_match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        return None


class MultiAgentOrchestrator:
    """Orchestrates Explorer, Advocate, and Reviewer agents."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.explorer = ExplorerAgent(self.settings)
        self.advocate = AdvocateAgent(self.settings)
        self.reviewer = ReviewerAgent(self.settings)

    async def run(
        self,
        query: str,
        user_description: str = "",
        profile: UnifiedAnimeProfile | None = None,
        on_tool_call: Any = None,
        on_progress: Any = None,
        on_commentary: Any = None,
    ) -> dict[str, Any]:
        if on_commentary:
            await on_commentary("## Multi-Agent System: Starting...")

        if on_progress:
            await on_progress(0, [], "Explorer searching...")

        exploration = await self.explorer.search(query, user_description, profile, on_commentary)

        if on_tool_call:
            await on_tool_call("explorer_search", {"query": query}, "done", {
                "total_found": exploration.get("total_found", 0),
            })

        if on_progress:
            await on_progress(1, [], f"Advocate evaluating {exploration.get('total_found', 0)} candidates...")

        advocacy = await self.advocate.evaluate(
            exploration.get("candidates", []),
            query,
            user_description,
            on_commentary,
        )

        if on_tool_call:
            await on_tool_call("advocate_evaluate", {"candidates": len(exploration.get("candidates", []))}, "done", {
                "selected": advocacy.get("selected_count", 0),
            })

        if on_progress:
            await on_progress(2, [], f"Reviewer finalizing {advocacy.get('selected_count', 0)} picks...")

        review = await self.reviewer.review(
            advocacy.get("top_picks", []),
            query,
            user_description,
            None,
            on_commentary,
        )

        if on_tool_call:
            await on_tool_call("reviewer_finalize", {"picks": len(advocacy.get("top_picks", []))}, "done", {
                "final_count": len(review.get("top_50", [])),
            })

        result = {
            "engine": "multi-agent",
            "source_title": query,
            "top_50": review.get("top_50", []),
            "commentary": review.get("commentary", []),
            "exploration_stats": {
                "total_found": exploration.get("total_found", 0),
                "evaluated": advocacy.get("evaluated_count", 0),
                "selected": advocacy.get("selected_count", 0),
                "final": len(review.get("top_50", [])),
            },
        }

        if on_commentary:
            await on_commentary(f"## Multi-Agent: Complete! {len(result['top_50'])} recommendations")

        return result
