from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from anime_search.models import UnifiedAnimeProfile

GENRE_THEME_HINTS = {
    "action": ("battle pacing", "high-energy conflict"),
    "adventure": ("journey structure", "exploration"),
    "comedy": ("comic timing", "lighter emotional texture"),
    "drama": ("character drama", "emotional stakes"),
    "fantasy": ("fantasy world", "magic or mythic systems"),
    "sci-fi": ("technology", "speculative setting"),
    "science fiction": ("technology", "speculative setting"),
    "romance": ("relationship focus", "romantic tension"),
    "slice of life": ("daily-life rhythm", "quiet character moments"),
    "supernatural": ("supernatural rules", "mystery powers"),
    "mystery": ("investigation", "reveal-driven story"),
    "psychological": ("inner conflict", "mind games"),
    "sports": ("team growth", "competitive arcs"),
    "music": ("performance energy", "music focus"),
    "mecha": ("robot combat", "pilot bonds"),
    "military": ("war strategy", "military hierarchy"),
    "isekai": ("another world", "power fantasy"),
    "harem": ("romantic rivalry", "multiple suitors"),
    "school": ("school life", "youth drama"),
    "horror": ("fear atmosphere", "survival horror"),
    "thriller": ("tension building", "high stakes"),
    "ecchi": ("fanservice", "romantic comedy"),
    "historical": ("period setting", "historical events"),
    "vampire": ("bloodlust", "immortal conflict"),
    "samurai": ("swordsmanship", "honor code"),
    "martial arts": ("combat mastery", "training arc"),
}

MIN_RECOMMENDATIONS = 3

DIMENSION_WEIGHTS = {
    "story_similarity": 0.15,
    "character_similarity": 0.12,
    "world_similarity": 0.10,
    "theme_similarity": 0.12,
    "power_system_similarity": 0.08,
    "emotional_similarity": 0.12,
    "art_style_similarity": 0.08,
    "music_similarity": 0.05,
    "pacing_similarity": 0.06,
    "tone_similarity": 0.05,
    "audience_similarity": 0.04,
    "genre_blend_similarity": 0.03,
}


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def key_text(value: Any) -> str:
    return normalize_text(value).casefold()


def source_title(profile: UnifiedAnimeProfile) -> str:
    return profile.get_primary_title()


def token_set(values: list[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        text = key_text(value)
        if text:
            tokens.add(text)
            tokens.update(part for part in re.split(r"[^a-z0-9]+", text) if len(part) > 2)
    return tokens


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def recommendation_candidates(profile: UnifiedAnimeProfile) -> list[dict[str, Any]]:
    source = key_text(source_title(profile))
    grouped: dict[str, dict[str, Any]] = {}
    source_hits: defaultdict[str, set[str]] = defaultdict(set)

    for item in profile.recommendations:
        title = normalize_text(item.get("title"))
        if not title or key_text(title) == source:
            continue
        key = key_text(title)
        candidate = grouped.setdefault(
            key,
            {
                "title": title,
                "url": item.get("url"),
                "provider_score": 0.0,
                "provider_sources": [],
                "raw": [],
            },
        )
        candidate["raw"].append(item)
        provider = normalize_text(item.get("source") or "provider")
        if provider not in source_hits[key]:
            candidate["provider_sources"].append(provider)
            source_hits[key].add(provider)
        if item.get("score"):
            try:
                candidate["provider_score"] += float(item["score"]) / 100
            except (TypeError, ValueError):
                pass
        candidate["provider_score"] += 1

    return list(grouped.values())


def source_genre_set(profile: UnifiedAnimeProfile) -> set[str]:
    return token_set(profile.genres)


def source_theme_set(profile: UnifiedAnimeProfile) -> set[str]:
    return token_set(profile.themes)


def candidate_dimensions(
    profile: UnifiedAnimeProfile,
    candidate: dict[str, Any],
    src_genres: set[str],
    src_themes: set[str],
) -> dict[str, int]:
    cand_genres = token_set(candidate.get("genres") or profile.genres)
    cand_themes = token_set(candidate.get("themes") or profile.themes)
    genre_overlap = jaccard_similarity(src_genres, cand_genres)
    theme_overlap = jaccard_similarity(src_themes, cand_themes)
    combined_overlap = jaccard_similarity(src_genres | src_themes, cand_genres | cand_themes)

    provider_count = len(candidate.get("provider_sources") or [])
    provider_score = candidate.get("provider_score", 0)
    provider_boost = min(15, provider_count * 5 + min(int(provider_score * 3), 10))

    description = key_text(profile.description.get("summary"))
    character_count = len(profile.characters)

    story = 50 + round(genre_overlap * 30) + round(theme_overlap * 20) + provider_boost
    character = 45 + min(35, character_count * 3) + round(genre_overlap * 10) + provider_boost
    world = 40 + round(combined_overlap * 40) + provider_boost + (
        15 if bool(cand_genres & {"fantasy", "sci-fi", "science fiction", "supernatural", "adventure", "isekai"}) else 0
    )
    theme = 45 + round(theme_overlap * 40) + round(genre_overlap * 15) + provider_boost
    power = 35 + (
        25 if bool(cand_genres & {"action", "supernatural", "fantasy", "mecha", "martial arts"}) else 0
    ) + round(genre_overlap * 20) + provider_boost
    emotional = 40 + (
        20 if bool(cand_genres & {"drama", "romance", "psychological", "slice of life"}) else 0
    ) + round(theme_overlap * 20) + provider_boost
    art = 45 + min(35, len(profile.studios) * 8) + provider_boost
    music = 40 + (25 if "music" in cand_genres or "music" in cand_themes else 0) + provider_boost
    pacing = 45 + round(theme_overlap * 25) + provider_boost
    tone = 45 + (
        20 if bool(cand_genres & {"drama", "comedy", "horror", "thriller", "psychological"}) else 0
    ) + round(genre_overlap * 15) + provider_boost
    audience = 45 + round(genre_overlap * 30) + provider_boost
    genre_blend = 45 + round(genre_overlap * 35) + provider_boost

    return {
        "story_similarity": min(max(story, 20), 100),
        "character_similarity": min(max(character, 20), 100),
        "world_similarity": min(max(world, 20), 100),
        "theme_similarity": min(max(theme, 20), 100),
        "power_system_similarity": min(max(power, 20), 100),
        "emotional_similarity": min(max(emotional, 20), 100),
        "art_style_similarity": min(max(art, 20), 100),
        "music_similarity": min(max(music, 20), 100),
        "pacing_similarity": min(max(pacing, 20), 100),
        "tone_similarity": min(max(tone, 20), 100),
        "audience_similarity": min(max(audience, 20), 100),
        "genre_blend_similarity": min(max(genre_blend, 20), 100),
    }


def calculate_weighted_score(dimensions: dict[str, int]) -> float:
    total = 0.0
    for dim, weight in DIMENSION_WEIGHTS.items():
        total += dimensions.get(dim, 50) * weight
    return round(total, 1)


def match_reason_for(
    profile: UnifiedAnimeProfile,
    candidate: dict[str, Any],
    dims: dict[str, int],
    genre_overlap: float,
    theme_overlap: float,
) -> str:
    src_genres = source_genre_set(profile)
    cand_genres = token_set(candidate.get("genres") or profile.genres)
    shared = src_genres & cand_genres
    providers = candidate.get("provider_sources") or []
    provider_text = ", ".join(providers[:2])

    parts = []
    if shared:
        genre_names = list(shared)[:3]
        parts.append(f"shares {', '.join(genre_names)} genres")
    if theme_overlap > 0.1:
        parts.append("overlapping thematic elements")
    if len(providers) >= 2:
        parts.append(f"confirmed by {provider_text}")
    if dims.get("emotional_similarity", 0) > 65:
        parts.append("similar emotional tone")
    if dims.get("story_similarity", 0) > 65:
        parts.append("comparable narrative structure")

    if parts:
        return "Recommended: " + "; ".join(parts[:3]) + "."
    return f"Recommended by {provider_text or 'source recommendation'} with audience overlap."


def explanation_for(
    profile: UnifiedAnimeProfile,
    candidate: dict[str, Any],
    dims: dict[str, int],
    genre_overlap: float,
) -> str:
    signals = []
    for value in [*profile.genres[:4], *profile.themes[:6]]:
        hint = GENRE_THEME_HINTS.get(key_text(value))
        if hint:
            signals.extend(hint)
    signal_text = ", ".join(dict.fromkeys(signals[:4]))
    provider_text = ", ".join(candidate.get("provider_sources") or ["source recommendation"])
    provider_count = len(candidate.get("provider_sources") or [])

    weighted = calculate_weighted_score(dims)
    if weighted >= 70:
        strength = "Strong"
    elif weighted >= 55:
        strength = "Moderate"
    else:
        strength = "Light"

    if signal_text and genre_overlap > 0.2:
        return f"{strength} match: {provider_text} connected it with significant genre overlap ({genre_overlap:.0%}) around {signal_text}."
    if signal_text:
        return f"{strength} match: {provider_text} connected it with overlap around {signal_text}."
    return f"{strength} match: {provider_text} connected it to the merged profile with audience-similarity signals."


def connection_type_for(
    profile: UnifiedAnimeProfile,
    candidate: dict[str, Any],
    genre_overlap: float,
) -> str:
    providers = candidate.get("provider_sources") or []
    provider_count = len(providers)
    if provider_count >= 3:
        return "franchise"
    if provider_count >= 2 and genre_overlap > 0.3:
        return "studio"
    if genre_overlap > 0.4:
        return "genre"
    if genre_overlap > 0.15:
        return "theme"
    return "audience"


def synopsis_for(profile: UnifiedAnimeProfile, candidate: dict[str, Any]) -> str:
    provider_text = " and ".join(candidate.get("provider_sources") or ["anime databases"])
    provider_count = len(candidate.get("provider_sources") or [])
    src = source_title(profile)
    if provider_count >= 3:
        return f"Universally recommended across all data sources. Strong thematic and narrative overlap with {src}."
    if provider_count >= 2:
        return f"Recommended by {provider_text}. Shares core storytelling elements with {src}."
    return f"Recommended by {provider_text}. Shares similar themes and audience appeal with {src}."


def fallback_recommendations(profile: UnifiedAnimeProfile, limit: int = 50) -> dict[str, Any]:
    candidates = recommendation_candidates(profile)
    src_genres = source_genre_set(profile)
    src_themes = source_theme_set(profile)
    source_counts = Counter(source for item in candidates for source in item.get("provider_sources", []))
    total_providers = len(profile.provider_status)

    seen_genres: list[str] = []
    ranked: list[dict[str, Any]] = []

    for candidate in candidates:
        dims = candidate_dimensions(profile, candidate, src_genres, src_themes)
        weighted_score = calculate_weighted_score(dims)

        cand_genres = token_set(candidate.get("genres") or profile.genres)
        cand_themes = token_set(candidate.get("themes") or profile.themes)
        genre_overlap = jaccard_similarity(src_genres, cand_genres)
        theme_overlap = jaccard_similarity(src_themes, cand_themes)

        provider_count = len(candidate.get("provider_sources") or [])
        provider_score = candidate.get("provider_score", 0)
        consensus_bonus = provider_count * 110
        popularity_bonus = min(provider_score * 30, 200)
        source_diversity_bonus = sum(
            10 for source in candidate.get("provider_sources", []) if source_counts[source] > 1
        )
        consensus_ratio = provider_count / max(total_providers, 1)
        consensus_multiplier = 1.0 + (consensus_ratio * 0.3)
        base = 480 + consensus_bonus + popularity_bonus + source_diversity_bonus
        base = round(base * consensus_multiplier)
        source_rank_noise = 1 / math.sqrt(len(candidate.get("raw") or []) + 1)
        score = max(0, min(1178, round(base - source_rank_noise)))

        diversity_bonus = 0
        cand_genre_key = "_".join(sorted(cand_genres))
        if cand_genre_key in seen_genres:
            diversity_bonus = -30
        else:
            diversity_bonus = 20
            seen_genres.append(cand_genre_key)

        score = max(0, min(1178, score + diversity_bonus))
        percentage = round((score / 1178) * 100, 1)
        confidence = min(100, round(48 + provider_count * 15 + min(provider_score * 5, 25)))
        rating = min(100, round(weighted_score))

        ranked.append(
            {
                "title": candidate["title"],
                "url": candidate.get("url"),
                "score": score,
                "similarity_percentage": percentage,
                "confidence_score": confidence,
                "rating": rating,
                "weighted_score": weighted_score,
                "dims": dims,
                "candidate": candidate,
                "genre_overlap": genre_overlap,
                "theme_overlap": theme_overlap,
            }
        )

    ranked.sort(key=lambda item: (item["score"], item["confidence_score"], item["title"]), reverse=True)
    top = []
    for rank, item in enumerate(ranked[:limit], start=1):
        candidate = item.pop("candidate")
        dims = item.pop("dims")
        genre_overlap = item.pop("genre_overlap")
        theme_overlap = item.pop("theme_overlap")
        top.append(
            {
                "rank": rank,
                "title": item["title"],
                "url": item.get("url"),
                "similarity_score_0_1178": item["score"],
                "similarity_percentage": item["similarity_percentage"],
                "rating": item["rating"],
                "weighted_score": item["weighted_score"],
                "synopsis": synopsis_for(profile, candidate),
                **dims,
                "overall_explanation": explanation_for(profile, candidate, dims, genre_overlap),
                "match_reason": match_reason_for(profile, candidate, dims, genre_overlap, theme_overlap),
                "confidence_score": item["confidence_score"],
                "genres": profile.genres[:6],
                "evidence": evidence_for(profile, candidate, dims, genre_overlap),
                "provider_sources": candidate.get("provider_sources", []),
                "connection_type": connection_type_for(profile, candidate, genre_overlap),
            }
        )

    return {
        "engine": "provider-similarity-fallback",
        "source_title": source_title(profile),
        "top_50": top,
        "notes": [
            f"Ranked {len(top)} recommendations from {len(candidates)} candidates across {total_providers} providers.",
        ],
    }


def evidence_for(
    profile: UnifiedAnimeProfile,
    candidate: dict[str, Any],
    dims: dict[str, int],
    genre_overlap: float,
) -> list[str]:
    evidence: list[str] = []
    providers = candidate.get("provider_sources") or []
    if providers:
        evidence.append(f"Recommended by {', '.join(providers[:3])}.")
    provider_count = len(providers)
    if provider_count >= 3:
        evidence.append("Strong cross-provider consensus (all 3 sources agree).")
    elif provider_count >= 2:
        evidence.append("Multi-source agreement boosts confidence.")
    if genre_overlap > 0.3:
        evidence.append(f"High genre overlap ({genre_overlap:.0%}) with source anime.")
    elif genre_overlap > 0.1:
        evidence.append(f"Moderate genre overlap ({genre_overlap:.0%}) with source anime.")
    if profile.studios:
        evidence.append(f"Art-style confidence boosted by studio data: {', '.join(profile.studios[:3])}.")
    weighted = calculate_weighted_score(dims)
    if weighted >= 70:
        evidence.append(f"Strong weighted similarity score ({weighted:.0f}/100).")
    return evidence[:4]


_NON_ANIME_TYPES = {"special", "music", "pv", "cm"}
_JUNK_TITLE_RE = re.compile(
    r"(?:best\s*\d+|top\s*\d+|opening|ending|pv|cm|preview|bonus|extra|oad)",
    re.IGNORECASE,
)


def _is_valid_anime_item(item: dict[str, Any]) -> bool:
    anime_type = (item.get("type") or "").lower()
    if anime_type in _NON_ANIME_TYPES:
        return False
    title = item.get("title") or ""
    if _JUNK_TITLE_RE.search(title):
        return False
    return True


def normalize_ai_recommendations(payload: dict[str, Any], profile: UnifiedAnimeProfile) -> dict[str, Any]:
    top = payload.get("top_50") or payload.get("top_25") or payload.get("top_20")
    if not isinstance(top, list):
        raise ValueError("AI recommendation response did not include a top_50 array.")

    normalized = {
        "engine": payload.get("engine") or "local-ai",
        "source_title": payload.get("source_title") or source_title(profile),
        "top_50": [],
        "notes": payload.get("notes", []),
        "thinking": payload.get("thinking", ""),
    }
    seen_titles: set[str] = set()
    for index, item in enumerate(top[:50], start=1):
        if not isinstance(item, dict) or not item.get("title"):
            continue
        if not _is_valid_anime_item(item):
            continue
        title = normalize_text(item.get("title"))
        if title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        score = int(max(0, min(1178, item.get("similarity_score_0_1178") or item.get("score") or 0)))
        percent = item.get("similarity_percentage")
        if percent is None:
            percent = round((score / 1178) * 100, 1)
        rating = item.get("rating")
        if rating is None:
            rating = min(100, round(score / 1178 * 80 + 20))
        match_reason = normalize_text(item.get("match_reason"))
        dims = {
            "story_similarity": int(item.get("story_similarity") or 0),
            "character_similarity": int(item.get("character_similarity") or 0),
            "world_similarity": int(item.get("world_similarity") or 0),
            "theme_similarity": int(item.get("theme_similarity") or 0),
            "power_system_similarity": int(item.get("power_system_similarity") or 0),
            "emotional_similarity": int(item.get("emotional_similarity") or 0),
            "art_style_similarity": int(item.get("art_style_similarity") or 0),
            "music_similarity": int(item.get("music_similarity") or 0),
            "pacing_similarity": int(item.get("pacing_similarity") or 0),
            "tone_similarity": int(item.get("tone_similarity") or 0),
            "audience_similarity": int(item.get("audience_similarity") or 0),
            "genre_blend_similarity": int(item.get("genre_blend_similarity") or 0),
        }
        weighted = calculate_weighted_score(dims)
        normalized["top_50"].append(
            {
                "rank": int(item.get("rank") or index),
                "title": title,
                "url": item.get("url"),
                "poster": item.get("poster"),
                "synopsis": normalize_text(item.get("synopsis")),
                "match_reason": match_reason,
                "rating": int(max(0, min(100, rating))),
                "weighted_score": weighted,
                "similarity_score_0_1178": score,
                "similarity_percentage": float(percent),
                **dims,
                "overall_explanation": normalize_text(item.get("overall_explanation")),
                "confidence_score": int(max(0, min(100, item.get("confidence_score") or 0))),
                "genres": item.get("genres") if isinstance(item.get("genres"), list) else profile.genres[:6],
                "themes": item.get("themes") if isinstance(item.get("themes"), list) else profile.themes[:6],
                "evidence": item.get("evidence") if isinstance(item.get("evidence"), list) else [],
                "connection_type": normalize_text(item.get("connection_type")) or "audience",
            }
        )

    if len(normalized["top_50"]) < MIN_RECOMMENDATIONS:
        fallback = fallback_recommendations(profile)
        existing_titles = {r["title"].lower() for r in normalized["top_50"]}
        for fb_item in fallback.get("top_50", []):
            if len(normalized["top_50"]) >= MIN_RECOMMENDATIONS:
                break
            if fb_item["title"].lower() not in existing_titles:
                normalized["top_50"].append(fb_item)
                existing_titles.add(fb_item["title"].lower())

    if not normalized["top_50"]:
        raise ValueError("AI recommendation response contained no usable recommendations.")
    return normalized
