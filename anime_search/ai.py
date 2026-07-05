from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator

import httpx

from anime_search.config import Settings
from anime_search.models import UnifiedAnimeProfile

log = logging.getLogger(__name__)

RECOMMENDATION_PROMPT = """\
You are an elite anime recommendation engine. You receive a merged anime profile from multiple databases.
Analyze the profile and recommend similar anime.

RULES:
1. USE YOUR OWN BRAIN FIRST: Think deeply about what makes this anime unique.
2. If the source profile has recommendations, use them as a starting point, but you MUST inject your own memory and critical opinions.
3. Rank by overall similarity: story, characters, themes, tone, genres.
4. Each recommendation MUST include a short explanation featuring your own personal critical opinion.
5. Return AT LEAST 3 recommendations (more is better).
6. Return ONLY valid JSON. No markdown, no text outside JSON.

SCORING:
- 95-100%: Near-identical match
- 85-94%: Very strong match
- 75-84%: Strong match
- 65-74%: Good match
- 55-64%: Moderate match
- 45-54%: Light match

OUTPUT SCHEMA:
{
  "engine": "local-ai",
  "source_title": "<title>",
  "top_50": [
    {
      "rank": 1,
      "title": "<anime title>",
      "synopsis": "<1-2 sentence match explanation>",
      "rating": 85,
      "similarity_score_0_1178": 1000,
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
      "overall_explanation": "<2-3 sentence explanation>",
      "confidence_score": 90,
      "genres": ["Action", "Fantasy"],
      "connection_type": "franchise|studio|genre|theme|audience"
    }
  ]
}"""

DESCRIPTION_PROMPT = """\
You are an anime discovery engine. The user describes what they want.
Find anime that MATCH that description using your knowledge.

RULES:
1. USE YOUR OWN BRAIN FIRST: Think deeply about the description and scan your internal memory for perfect matches.
2. Parse the description: genre, tone, setting, characters, themes.
3. Return anime that match ALL aspects.
4. Each MUST explain WHY it matches, featuring your own critical opinion.
5. Return AT LEAST 3 recommendations.
6. Return ONLY valid JSON. No markdown, no text outside JSON.

OUTPUT SCHEMA:
{
  "engine": "local-ai",
  "source_title": "<description summary>",
  "top_50": [
    {
      "rank": 1,
      "title": "<anime title>",
      "synopsis": "<match explanation>",
      "rating": 85,
      "similarity_score_0_1178": 1000,
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
      "overall_explanation": "<explanation>",
      "confidence_score": 90,
      "genres": ["Genre1", "Genre2"],
      "connection_type": "genre|theme|audience"
    }
  ]
}"""


def _extract_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, str) and content.strip():
        return content.strip()
    reasoning = message.get("reasoning_content", "")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()
    return ""


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    depth = 0
    in_string = False
    escape = False
    last_array_close = -1
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ("{", "["):
            depth += 1
        elif ch in ("}", "]"):
            depth -= 1
            if ch == "]":
                last_array_close = i
    repaired = text
    if last_array_close > 0 and last_array_close < len(text) - 1:
        repaired = text[:last_array_close + 1]
    while depth > 0:
        repaired += "}"
        depth -= 1
    try:
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    return None


def parse_chat_completion_json(response_payload: dict[str, Any]) -> dict[str, Any]:
    choices = response_payload.get("choices") or []
    if not choices:
        raise ValueError("Local AI returned no choices.")
    content = _extract_text(choices[0].get("message", {}))
    if not content:
        raise ValueError("Local AI returned an empty response.")
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    cleaned = re.sub(r"```(?:json)?\s*", "", content)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        repaired = _repair_truncated_json(candidate)
        if repaired:
            return repaired
    raise ValueError(f"Local AI returned invalid JSON. First 200 chars: {content[:200]}")


def parse_streaming_chunks(chunks: list[str]) -> dict[str, Any]:
    combined = "".join(chunks).strip()
    if not combined:
        raise ValueError("Streaming response was empty.")
    try:
        return json.loads(combined)
    except json.JSONDecodeError:
        pass
    cleaned = re.sub(r"```(?:json)?\s*", "", combined)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", combined)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        repaired = _repair_truncated_json(candidate)
        if repaired:
            return repaired
    raise ValueError(f"Could not parse streaming JSON. First 200 chars: {combined[:200]}")


async def _detect_model(client: httpx.AsyncClient, base_url: str) -> str | None:
    try:
        resp = await client.get(f"{base_url}/models")
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("id") for m in data.get("data", []) if m.get("id")]
        non_thinking = [
            m for m in models
            if not any(k in m.lower() for k in ("qwen3", "qwq", "deepseek-r1", "o1", "o3"))
        ]
        if non_thinking:
            for preferred in ["google/gemma-4-e2b", "google/gemma-3-4b", "google/gemma-3-27b"]:
                if preferred in non_thinking:
                    return preferred
            return non_thinking[0]
        return models[0] if models else None
    except Exception:
        return None


async def _call_chat(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    max_retries: int = 2,
) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            raw_data = response.json()
            usage = raw_data.get("usage", {})
            return parse_chat_completion_json(raw_data), usage
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 400:
                text = exc.response.text.lower()
                if "response_format" in text:
                    payload = {k: v for k, v in payload.items() if k != "response_format"}
                    continue
                if "model" in text:
                    raise ValueError(f"Model '{payload.get('model')}' not available.") from exc
                raise
            if status == 429 and attempt < max_retries:
                retry_after = float(exc.response.headers.get("Retry-After", "3"))
                await asyncio.sleep(retry_after)
                continue
            if status == 503 and attempt < max_retries:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            if status >= 500 and attempt < max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise ValueError(
                f"Cannot connect to LM Studio at {url}. "
                "Make sure LM Studio is running and a model is loaded."
            ) from exc
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise ValueError("LM Studio request timed out.") from exc
    raise last_error or RuntimeError("LM Studio call failed after retries.")


async def _call_chat_streaming(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    max_retries: int = 2,
) -> AsyncIterator[str]:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            payload["stream"] = True
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue
            return
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 400:
                text = exc.response.text.lower()
                if "response_format" in text:
                    payload.pop("response_format", None)
                    continue
                if "stream" in text:
                    payload.pop("stream", None)
                    result, _ = await _call_chat(client, url, headers, payload, max_retries=0)
                    yield json.dumps(result)
                    return
                raise
            if status >= 500 and attempt < max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_error = exc
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise ValueError(f"Cannot connect to LM Studio.") from exc
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt < max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise ValueError("LM Studio request timed out.") from exc
    raise last_error or RuntimeError("LM Studio call failed after retries.")


def recommendation_seed(profile: UnifiedAnimeProfile, user_description: str = "") -> dict[str, Any]:
    desc = profile.description.get("summary", "") or ""
    if user_description:
        desc = f"{desc}\n\nUser context: {user_description}" if desc else user_description
    if len(desc) > 1200:
        desc = desc[:1200] + "..."
    return {
        "source_title": profile.get_primary_title(),
        "titles": profile.titles,
        "description": desc,
        "genres": profile.genres[:20],
        "themes": profile.themes[:20],
        "studios": profile.studios[:15],
        "characters": [
            {
                "name": c.get("name"),
                "role": c.get("role"),
                "description": (c.get("description") or "")[:300],
            }
            for c in profile.characters[:40]
        ],
        "recommendations": profile.recommendations[:60],
        "statistics": profile.statistics,
        "release": profile.release,
    }


def description_seed(user_description: str) -> dict[str, Any]:
    return {
        "user_description": user_description,
        "instructions": "Find anime matching this description. Return at least 3 results.",
    }


async def recommend_with_local_ai(
    profile: UnifiedAnimeProfile,
    settings: Settings,
    user_description: str = "",
) -> dict[str, Any]:
    base_url = settings.effective_ai_base_url.rstrip("/")
    url = f"{base_url}/v1/chat/completions"
    headers = settings.llm_headers

    async with httpx.AsyncClient(timeout=settings.ai_http_timeout) as client:
        model = settings.effective_ai_model
        if not model:
            detected = await _detect_model(client, base_url)
            if not detected:
                raise ValueError("No model configured and none detected on LM Studio.")
            model = detected
            log.info("Auto-detected LM Studio model: %s", model)

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": RECOMMENDATION_PROMPT},
                {"role": "user", "content": json.dumps(recommendation_seed(profile, user_description), ensure_ascii=False)},
            ],
            "temperature": settings.ai_temperature,
            "max_tokens": settings.ai_max_tokens,
        }
        result, _ = await _call_chat(client, url, headers, payload, max_retries=settings.max_retries)
        return result


async def recommend_with_local_ai_streaming(
    profile: UnifiedAnimeProfile,
    settings: Settings,
    user_description: str = "",
) -> AsyncIterator[str]:
    base_url = settings.effective_ai_base_url.rstrip("/")
    url = f"{base_url}/v1/chat/completions"
    headers = settings.llm_headers

    async with httpx.AsyncClient(timeout=settings.ai_http_timeout) as client:
        model = settings.effective_ai_model
        if not model:
            detected = await _detect_model(client, base_url)
            if not detected:
                raise ValueError("No model configured and none detected on LM Studio.")
            model = detected

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": RECOMMENDATION_PROMPT},
                {"role": "user", "content": json.dumps(recommendation_seed(profile, user_description), ensure_ascii=False)},
            ],
            "temperature": settings.ai_temperature,
            "max_tokens": settings.ai_max_tokens,
        }
        async for chunk in _call_chat_streaming(client, url, headers, payload, max_retries=settings.max_retries):
            yield chunk


async def search_by_description(
    settings: Settings,
    user_description: str,
) -> dict[str, Any]:
    base_url = settings.effective_ai_base_url.rstrip("/")
    url = f"{base_url}/v1/chat/completions"
    headers = settings.llm_headers

    async with httpx.AsyncClient(timeout=settings.ai_http_timeout) as client:
        model = settings.effective_ai_model
        if not model:
            detected = await _detect_model(client, base_url)
            if not detected:
                raise ValueError("No model configured and none detected on LM Studio.")
            model = detected

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": DESCRIPTION_PROMPT},
                {"role": "user", "content": json.dumps(description_seed(user_description), ensure_ascii=False)},
            ],
            "temperature": 0.3,
            "max_tokens": settings.ai_max_tokens,
        }
        result, _ = await _call_chat(client, url, headers, payload, max_retries=settings.max_retries)
        return result


async def agent_recommend(
    profile: UnifiedAnimeProfile,
    settings: Settings,
    user_description: str = "",
    on_tool_call: Any = None,
    on_progress: Any = None,
    on_commentary: Any = None,
) -> dict[str, Any]:
    from anime_search.agent import AnimeAgent
    agent = AnimeAgent(settings)
    desc = profile.description.get("summary", "") or ""
    if user_description:
        desc = f"{desc}\n\nUser context: {user_description}" if desc else user_description
    query = profile.query or profile.titles.get("all", [""])[0] or desc[:80]
    return await agent.research(query, user_description or desc, profile=profile, on_tool_call=on_tool_call, on_progress=on_progress, on_commentary=on_commentary)
