from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from anime_search.models import SourceResult, UnifiedAnimeProfile


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    compact = re.sub(r"\s+", " ", value).strip()
    return compact or None


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def dedupe_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = clean_text(value)
        if not text:
            continue
        key = normalize_key(text)
        if key not in seen:
            seen.add(key)
            output.append(text)
    return output


def best_description(results: list[SourceResult]) -> dict[str, Any]:
    candidates: list[tuple[float, str, str]] = []
    for result in results:
        description = result.normalized.get("description", {})
        for label, text in description.items():
            clean = clean_text(text)
            if clean:
                candidates.append((result.confidence, label, clean))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: (len(item[2]), item[0]), reverse=True)
    return {"summary": candidates[0][2], "source_preference": candidates[0][1]}


def merge_named_dicts(items: Iterable[dict[str, Any]], key_field: str = "name") -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        name = clean_text(item.get(key_field) or item.get("title"))
        if not name:
            continue
        key = normalize_key(name)
        existing = merged.setdefault(key, {key_field: name})
        for field, value in item.items():
            if value in (None, "", [], {}):
                continue
            if field not in existing or existing[field] in (None, "", [], {}):
                existing[field] = value
            elif isinstance(existing[field], list) and isinstance(value, list):
                if field == "voice_actors":
                    existing[field] = merge_named_dicts(existing[field] + value, "name")
                else:
                    existing[field].extend(v for v in value if v not in existing[field])
    return list(merged.values())


def choose_media(results: list[SourceResult]) -> dict[str, Any]:
    media: dict[str, Any] = {}
    for result in sorted(results, key=lambda r: r.confidence, reverse=True):
        for key, value in (result.normalized.get("media") or {}).items():
            if value and key not in media:
                media[key] = value
    return media


def merge_statistics(results: list[SourceResult]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for result in successful(results):
        source_stats = result.normalized.get("statistics", {})
        if source_stats:
            stats[result.source] = source_stats
    return stats


def merge_release(results: list[SourceResult]) -> dict[str, Any]:
    release: dict[str, Any] = {}
    for result in successful(results):
        source_release = result.normalized.get("release", {})
        if source_release:
            release[result.source] = source_release
    return release


def successful(results: list[SourceResult]) -> list[SourceResult]:
    return [r for r in results if r.ok]


def merge_profiles(query: str, results: list[SourceResult]) -> UnifiedAnimeProfile:
    ok_results = successful(results)
    profile = UnifiedAnimeProfile(query=query)
    profile.source_confidence = {result.source: result.confidence for result in results}
    profile.provider_status = {
        result.source: {
            "ok": result.ok,
            "error": result.error,
            "confidence": result.confidence,
            "response_time_ms": result.response_time_ms,
        }
        for result in results
    }

    for title_key in ("english", "japanese", "romaji", "all"):
        profile.titles[title_key] = dedupe_strings(
            title
            for result in ok_results
            for title in (result.normalized.get("titles", {}).get(title_key) or [])
        )

    profile.description = best_description(ok_results)
    profile.genres = dedupe_strings(
        value for result in ok_results for value in result.normalized.get("genres", [])
    )
    profile.themes = dedupe_strings(
        value for result in ok_results for value in result.normalized.get("themes", [])
    )
    profile.studios = dedupe_strings(
        value for result in ok_results for value in result.normalized.get("studios", [])
    )
    profile.producers = dedupe_strings(
        value for result in ok_results for value in result.normalized.get("producers", [])
    )
    profile.characters = merge_named_dicts(
        item for result in ok_results for item in result.normalized.get("characters", [])
    )
    profile.staff = merge_named_dicts(
        item for result in ok_results for item in result.normalized.get("staff", [])
    )
    profile.recommendations = merge_named_dicts(
        (item for result in ok_results for item in result.normalized.get("recommendations", [])),
        key_field="title",
    )
    profile.media = choose_media(ok_results)
    profile.relationships = [
        item for result in ok_results for item in result.normalized.get("relationships", [])
    ]
    profile.external_links = [
        item for result in ok_results for item in result.normalized.get("external_links", [])
    ]
    profile.streaming_services = [
        item for result in ok_results for item in result.normalized.get("streaming_services", [])
    ]
    profile.statistics = merge_statistics(ok_results)
    profile.release = merge_release(ok_results)

    total_confidence = sum(r.confidence for r in ok_results)
    profile.confidence_score = total_confidence / max(len(ok_results), 1)

    return profile
