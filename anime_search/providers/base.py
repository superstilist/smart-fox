from __future__ import annotations

import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from anime_search.cache import SQLiteJsonCache
from anime_search.config import Settings
from anime_search.models import SourceResult
from anime_search.ratelimit import RateLimiter

log = logging.getLogger(__name__)

NSFW_GENRES = {"ecchi", "hentai", "erotica", "adult cast"}
NSFW_THEMES = {"harem"}


class ProviderError(Exception):
    def __init__(self, provider: str, message: str, retryable: bool = False) -> None:
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"{provider}: {message}")


class ProviderNoResult(ProviderError):
    def __init__(self, provider: str, query: str) -> None:
        super().__init__(provider, f"no results for query: {query}", retryable=False)


class ProviderBlocked(ProviderError):
    def __init__(self, provider: str, status: int) -> None:
        super().__init__(provider, f"blocked (HTTP {status})", retryable=False)


class AnimeProvider(ABC):
    name: str
    confidence: float
    priority: int = 100

    _shared_rate_limiter: RateLimiter | None = None

    def __init__(self, client: httpx.AsyncClient, cache: SQLiteJsonCache, settings: Settings) -> None:
        self.client = client
        self.cache = cache
        self.settings = settings
        if AnimeProvider._shared_rate_limiter is None:
            AnimeProvider._shared_rate_limiter = RateLimiter()
        self._limiter = AnimeProvider._shared_rate_limiter

    @classmethod
    def reset_rate_limiter(cls) -> None:
        cls._shared_rate_limiter = None

    async def search(
        self,
        query: str,
        content_filter: str = "sfw",
        negative_prompt: str = "",
    ) -> SourceResult:
        if not self._limiter.can_request(self.name):
            wait = self._limiter.wait_time(self.name)
            log.warning("%s: skipping, cooldown %.0fs remaining", self.name, wait)
            return SourceResult(
                source=self.name,
                confidence=self.confidence,
                query=query,
                ok=False,
                error=f"rate limit cooldown ({wait:.0fs} remaining)",
            )

        cache_key = f"{query.strip().lower()}|{content_filter}|{negative_prompt}"
        cached = self.cache.get(self.name, cache_key)
        if cached is not None:
            cached["ok"] = True
            cached["error"] = None
            log.debug("%s: cache hit for '%s'", self.name, query)
            return SourceResult.model_validate(cached)

        last_error: str | None = None
        max_retries = min(self.settings.max_retries, 2)

        for attempt in range(max_retries + 1):
            start_time = time.monotonic()
            try:
                raw = await self.fetch(query, content_filter=content_filter)
                elapsed_ms = (time.monotonic() - start_time) * 1000
                normalized = self.normalize(query, raw)

                if not self._has_meaningful_data(normalized):
                    raise ProviderNoResult(self.name, query)

                if content_filter == "sfw":
                    normalized = self._filter_nsfw(normalized)
                if negative_prompt:
                    normalized = self._apply_negative_prompt(normalized, negative_prompt)

                result = SourceResult(
                    source=self.name,
                    confidence=self.confidence,
                    query=query,
                    ok=True,
                    raw=raw,
                    normalized=normalized,
                    response_time_ms=elapsed_ms,
                )
                self.cache.set(self.name, cache_key, result.model_dump(mode="json"))
                self._limiter.record_success(self.name)
                log.info("%s: OK for '%s' (%.0fms)", self.name, query, elapsed_ms)
                return result

            except ProviderNoResult as exc:
                last_error = str(exc)
                self._limiter.record_success(self.name)
                log.info("%s: no results for '%s'", self.name, query)
                break

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429:
                    self._limiter.record_failure(self.name, is_rate_limit=True)
                    retry_after = float(exc.response.headers.get("Retry-After", "0"))
                    delay = max(retry_after, 2 ** (attempt + 1))
                    log.warning("%s: 429 rate limited, backoff %.1fs", self.name, delay)
                    if attempt < max_retries:
                        await asyncio.sleep(delay)
                        continue
                    last_error = f"rate limited (429)"
                    break
                elif status == 403:
                    self._limiter.record_blocked(self.name)
                    last_error = f"blocked (403)"
                    log.warning("%s: blocked (403), provider disabled for 120s", self.name)
                    break
                elif status == 400:
                    self._limiter.record_failure(self.name)
                    last_error = f"bad request (400)"
                    log.warning("%s: bad request (400) for '%s'", self.name, query)
                    break
                elif status >= 500:
                    self._limiter.record_failure(self.name)
                    if attempt < max_retries:
                        delay = 1.0 * (attempt + 1)
                        log.warning("%s: server error %d, retrying in %.1fs", self.name, status, delay)
                        await asyncio.sleep(delay)
                        continue
                    last_error = f"server error ({status})"
                    break
                else:
                    self._limiter.record_failure(self.name)
                    last_error = f"HTTP {status}"
                    break

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException) as exc:
                self._limiter.record_failure(self.name)
                last_error = f"connection error: {exc}"
                if attempt < max_retries:
                    delay = 1.0 * (attempt + 1)
                    log.warning("%s: connection error, retrying in %.1fs", self.name, delay)
                    await asyncio.sleep(delay)
                    continue
                break

            except ProviderError as exc:
                last_error = str(exc)
                self._limiter.record_failure(self.name)
                if exc.retryable and attempt < max_retries:
                    delay = 1.0 * (attempt + 1)
                    log.warning("%s: retryable error, retrying in %.1fs", self.name, delay)
                    await asyncio.sleep(delay)
                    continue
                break

            except Exception as exc:
                last_error = f"unexpected: {exc}"
                self._limiter.record_failure(self.name)
                log.warning("%s: unexpected error: %s", self.name, exc)
                break

        return SourceResult(
            source=self.name,
            confidence=self.confidence,
            query=query,
            ok=False,
            error=last_error or "unknown error",
        )

    @abstractmethod
    async def fetch(self, query: str, content_filter: str = "sfw") -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, query: str, raw: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _has_meaningful_data(self, normalized: dict[str, Any]) -> bool:
        titles = normalized.get("titles", {})
        has_title = any(
            titles.get(key)
            for key in ("english", "japanese", "romaji", "all")
        )
        has_genres = bool(normalized.get("genres"))
        has_description = bool(normalized.get("description", {}).get("summary"))
        return has_title or has_genres or has_description

    def _filter_nsfw(self, normalized: dict[str, Any]) -> dict[str, Any]:
        genres = [g for g in normalized.get("genres", []) if g.lower() not in NSFW_GENRES]
        themes = [t for t in normalized.get("themes", []) if t.lower() not in NSFW_THEMES]
        normalized["genres"] = genres
        normalized["themes"] = themes
        return normalized

    def _apply_negative_prompt(self, normalized: dict[str, Any], negative_prompt: str) -> dict[str, Any]:
        exclude_terms = [t.strip().lower() for t in negative_prompt.split(",") if t.strip()]
        if not exclude_terms:
            return normalized
        genres = [
            g for g in normalized.get("genres", [])
            if not any(term in g.lower() for term in exclude_terms)
        ]
        themes = [
            t for t in normalized.get("themes", [])
            if not any(term in t.lower() for term in exclude_terms)
        ]
        normalized["genres"] = genres
        normalized["themes"] = themes
        return normalized


def compact_html(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<br\s*/?>", "\n", value)
    text = re.sub(r"</?[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def extract_image(images: dict[str, Any]) -> str | None:
    for source in ("jpg", "webp"):
        img_data = images.get(source, {})
        for key in ("large_image_url", "image_url"):
            url = img_data.get(key)
            if url:
                return url
    return None


def normalize_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
        if 0 <= score <= 100:
            return score
        return None
    except (TypeError, ValueError):
        return None
