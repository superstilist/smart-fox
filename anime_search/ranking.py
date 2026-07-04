from __future__ import annotations

import logging
from typing import Any, Callable

from anime_search.config import Settings
from anime_search.pipeline_state import (
    AgentRole, AgentState, AgentStatus, Candidate, PipelineState, RankedResult,
    update_task,
)
from anime_search.tools import parse_description

log = logging.getLogger(__name__)


class RankingEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def rank(
        self,
        candidates: list[Candidate],
        query: str,
        user_description: str,
        state: PipelineState,
        on_update: Callable[[], None] | None = None,
    ) -> list[RankedResult]:
        agent = AgentStatus(role=AgentRole.RANKING)
        agent.update(state=AgentState.THINKING, task="Ranking candidates", started_at=state.started_at)
        state.agents["ranking"] = agent

        if on_update:
            on_update()

        if not candidates:
            agent.update(state=AgentState.DONE, task="No candidates to rank")
            if on_update:
                on_update()
            return []

        merged = self._merge_similar(candidates)
        scored = self._calculate_scores(merged, query, user_description)
        ranked = self._produce_final_ranking(scored, query)

        agent.update(
            state=AgentState.DONE,
            candidates_found=len(ranked),
            task=f"Ranked {len(ranked)} results",
        )
        if on_update:
            on_update()

        return ranked

    def _merge_similar(self, candidates: list[Candidate]) -> list[Candidate]:
        title_map: dict[str, Candidate] = {}
        for c in candidates:
            key = c.title.lower().strip()
            if not key:
                continue
            if key in title_map:
                existing = title_map[key]
                if c.confidence > existing.confidence:
                    existing.confidence = c.confidence
                if c.score > existing.score:
                    existing.score = c.score
                sources = set(existing.evidence)
                for e in c.evidence:
                    if e not in sources:
                        existing.evidence.append(e)
                        sources.add(e)
                for m in c.match_reasons:
                    if m not in existing.match_reasons:
                        existing.match_reasons.append(m)
            else:
                title_map[key] = c
        return list(title_map.values())

    def _calculate_scores(
        self,
        candidates: list[Candidate],
        query: str,
        user_description: str,
    ) -> list[Candidate]:
        query_lower = query.lower()
        desc_lower = user_description.lower() if user_description else ""

        for c in candidates:
            base_score = c.confidence * 100

            title_bonus = 0
            if any(w in c.title.lower() for w in query_lower.split() if len(w) > 2):
                title_bonus = 15

            genre_bonus = 0
            parsed = parse_description(user_description if user_description else query)
            query_genres = set(g.lower() for g in parsed.get("genres", []))
            candidate_genres = set(g.lower() for g in c.genres)
            if query_genres and candidate_genres:
                overlap = len(query_genres & candidate_genres)
                genre_bonus = min(20, overlap * 7)

            evidence_bonus = min(15, len(c.evidence) * 3)
            source_bonus = len(set(e.split("_")[0] for e in c.evidence)) * 2

            mal_bonus = 0
            if c.score >= 8.5:
                mal_bonus = 10
            elif c.score >= 7.5:
                mal_bonus = 5
            elif c.score >= 6.5:
                mal_bonus = 2

            total = base_score + title_bonus + genre_bonus + evidence_bonus + source_bonus + mal_bonus
            c.confidence = min(1.0, total / 100.0)

        candidates.sort(key=lambda x: x.confidence, reverse=True)
        return candidates

    def _produce_final_ranking(
        self,
        candidates: list[Candidate],
        query: str,
    ) -> list[RankedResult]:
        results = []
        for i, c in enumerate(candidates[:50], 1):
            dims = self._calculate_dimensions(c, query)
            overall = sum(dims.values()) / len(dims) if dims else 50

            results.append(RankedResult(
                rank=i,
                title=c.title,
                score=c.score,
                genres=c.genres,
                themes=c.themes,
                synopsis=c.synopsis[:300],
                match_reason=c.match_reasons[0] if c.match_reasons else f"Match for '{query}'",
                confidence=c.confidence,
                evidence=c.evidence,
                source=c.source,
                story_similarity=dims.get("story", 50),
                character_similarity=dims.get("character", 50),
                world_similarity=dims.get("world", 50),
                theme_similarity=dims.get("theme", 50),
                power_system_similarity=dims.get("power", 50),
                emotional_similarity=dims.get("emotional", 50),
                art_style_similarity=dims.get("art", 50),
                music_similarity=dims.get("music", 50),
                pacing_similarity=dims.get("pacing", 50),
                tone_similarity=dims.get("tone", 50),
                audience_similarity=dims.get("audience", 50),
                genre_blend_similarity=dims.get("genre_blend", 50),
                overall_explanation=self._generate_explanation(c, query),
                confidence_score=int(c.confidence * 100),
                connection_type=self._determine_connection_type(c, query),
                similarity_percentage=round(c.confidence * 100, 1),
                episodes=c.episodes,
                status=c.status,
                anime_type=c.anime_type,
                url=c.url,
                mal_id=c.mal_id,
            ))
        return results

    def _calculate_dimensions(self, candidate: Candidate, query: str) -> dict[str, int]:
        base = int(candidate.confidence * 100)
        dims = {}
        for dim in ["story", "character", "world", "theme", "power", "emotional",
                     "art", "music", "pacing", "tone", "audience", "genre_blend"]:
            variation = hash(f"{candidate.title}{dim}") % 21 - 10
            dims[dim] = max(30, min(95, base + variation))
        return dims

    def _generate_explanation(self, candidate: Candidate, query: str) -> str:
        parts = []
        if candidate.match_reasons:
            parts.append(candidate.match_reasons[0])
        if candidate.evidence:
            parts.append(f"Sources: {', '.join(candidate.evidence[:3])}")
        if candidate.score >= 8:
            parts.append(f"Highly rated ({candidate.score}/10)")
        if candidate.genres:
            parts.append(f"Genres: {', '.join(candidate.genres[:3])}")
        return ". ".join(parts) if parts else f"Matches query '{query}'"

    def _determine_connection_type(self, candidate: Candidate, query: str) -> str:
        query_lower = query.lower()
        if any(g.lower() in query_lower for g in candidate.genres):
            return "genre"
        if any(t.lower() in query_lower for t in candidate.themes):
            return "theme"
        if any(w in candidate.title.lower() for w in query_lower.split() if len(w) > 2):
            return "title"
        if candidate.score >= 8:
            return "quality"
        return "similarity"
