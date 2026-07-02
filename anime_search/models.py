from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SearchType(str, Enum):
    TITLE = "title"
    DESCRIPTION = "description"
    GENRE = "genre"
    THEME = "theme"
    CHARACTER = "character"
    STUDIO = "studio"
    KEYWORD = "keyword"
    UNKNOWN = "unknown"


class SourceResult(BaseModel):
    source: str
    confidence: float
    query: str
    search_type: SearchType = SearchType.UNKNOWN
    ok: bool = False
    error: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    normalized: dict[str, Any] = Field(default_factory=dict)
    response_time_ms: float = 0


class Character(BaseModel):
    name: str = ""
    native_name: str = ""
    role: str = ""
    description: str | None = None
    image: str | None = None
    hair_color: str | None = None
    eye_color: str | None = None
    personality: list[str] = Field(default_factory=list)
    abilities: list[str] = Field(default_factory=list)
    voice_actors: list[dict[str, Any]] = Field(default_factory=list)
    source: str = ""


class Recommendation(BaseModel):
    rank: int = 0
    title: str = ""
    url: str | None = None
    synopsis: str = ""
    rating: int = 0
    similarity_score_0_1178: int = 0
    similarity_percentage: float = 0
    story_similarity: int = 0
    character_similarity: int = 0
    world_similarity: int = 0
    theme_similarity: int = 0
    power_system_similarity: int = 0
    emotional_similarity: int = 0
    art_style_similarity: int = 0
    music_similarity: int = 0
    overall_explanation: str = ""
    confidence_score: int = 0
    genres: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    connection_type: str = "audience"
    poster: str | None = None


class UnifiedAnimeProfile(BaseModel):
    query: str
    search_type: SearchType = SearchType.UNKNOWN
    titles: dict[str, list[str]] = Field(default_factory=dict)
    description: dict[str, Any] = Field(default_factory=dict)
    genres: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    studios: list[str] = Field(default_factory=list)
    producers: list[str] = Field(default_factory=list)
    characters: list[dict[str, Any]] = Field(default_factory=list)
    staff: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    media: dict[str, Any] = Field(default_factory=dict)
    statistics: dict[str, Any] = Field(default_factory=dict)
    release: dict[str, Any] = Field(default_factory=dict)
    external_links: list[dict[str, Any]] = Field(default_factory=list)
    streaming_services: list[dict[str, Any]] = Field(default_factory=list)
    source_confidence: dict[str, float] = Field(default_factory=dict)
    provider_status: dict[str, dict[str, Any]] = Field(default_factory=dict)
    search_type_detected: SearchType = SearchType.UNKNOWN
    confidence_score: float = 0

    def get_primary_title(self) -> str:
        for key in ("english", "romaji", "all"):
            values = self.titles.get(key) or []
            if values:
                return values[0]
        return self.query

    def get_genres(self) -> list[str]:
        return self.genres[:10]

    def get_themes(self) -> list[str]:
        return self.themes[:10]

    def get_studios(self) -> list[str]:
        return self.studios[:5]

    def get_character_names(self) -> list[str]:
        return [c.get("name", "") for c in self.characters[:15] if c.get("name")]


class ToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    result: dict[str, Any] | None = None
    timestamp: float = 0


class TaskStatus(BaseModel):
    task_id: str
    status: str = "starting"
    progress: int = 0
    message: str = ""
    results: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    profile: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    created_at: float = 0
    query: str = ""
