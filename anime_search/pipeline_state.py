from __future__ import annotations

import asyncio
import enum
import time
import threading
from dataclasses import dataclass, field
from typing import Any


class AgentRole(enum.Enum):
    EXPLORER = "explorer"
    ADVOCATE = "advocate"
    REVIEWER = "reviewer"
    RANKING = "ranking"
    MERGE = "merge"


class AgentState(enum.Enum):
    IDLE = "idle"
    SEARCHING = "searching"
    THINKING = "thinking"
    DISCUSSING = "discussing"
    DONE = "done"
    ERROR = "error"


class PipelineStage(enum.Enum):
    INIT = "init"
    EXPLORING = "exploring"
    DISCUSSING = "discussing"
    RANKING = "ranking"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class AgentStatus:
    role: AgentRole
    state: AgentState = AgentState.IDLE
    task: str = ""
    progress: float = 0.0
    requests_made: int = 0
    requests_failed: int = 0
    candidates_found: int = 0
    started_at: float = 0.0
    last_update: float = 0.0
    messages: list[str] = field(default_factory=list)

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.last_update = time.time()

    def elapsed(self) -> float:
        if self.started_at <= 0:
            return 0.0
        return time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "state": self.state.value,
            "task": self.task,
            "progress": self.progress,
            "requests_made": self.requests_made,
            "requests_failed": self.requests_failed,
            "candidates_found": self.candidates_found,
            "elapsed": round(self.elapsed(), 1),
            "messages": self.messages[-10:],
        }


@dataclass
class DiscussionMessage:
    round_num: int
    speaker: AgentRole
    message: str
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp <= 0:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_num,
            "speaker": self.speaker.value,
            "message": self.message,
            "timestamp": self.timestamp,
        }


@dataclass
class Candidate:
    title: str
    score: float = 0.0
    genres: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    synopsis: str = ""
    source: str = ""
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    match_reasons: list[str] = field(default_factory=list)
    mal_id: int | None = None
    episodes: int | None = None
    status: str = ""
    anime_type: str = ""
    url: str = ""
    poster: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "score": self.score,
            "genres": self.genres,
            "themes": self.themes,
            "synopsis": self.synopsis,
            "source": self.source,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "match_reasons": self.match_reasons,
            "mal_id": self.mal_id,
            "episodes": self.episodes,
            "status": self.status,
            "type": self.anime_type,
            "url": self.url,
            "poster": self.poster,
        }


@dataclass
class RankedResult:
    rank: int
    title: str
    score: float
    genres: list[str]
    themes: list[str]
    synopsis: str
    match_reason: str
    confidence: float
    evidence: list[str]
    source: str
    story_similarity: int = 0
    character_similarity: int = 0
    world_similarity: int = 0
    theme_similarity: int = 0
    power_system_similarity: int = 0
    emotional_similarity: int = 0
    art_style_similarity: int = 0
    music_similarity: int = 0
    pacing_similarity: int = 0
    tone_similarity: int = 0
    audience_similarity: int = 0
    genre_blend_similarity: int = 0
    overall_explanation: str = ""
    confidence_score: int = 0
    connection_type: str = ""
    similarity_percentage: float = 0.0
    episodes: int | None = None
    status: str = ""
    anime_type: str = ""
    url: str = ""
    mal_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "title": self.title,
            "score": self.score,
            "genres": self.genres,
            "themes": self.themes,
            "synopsis": self.synopsis,
            "match_reason": self.match_reason,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "source": self.source,
            "story_similarity": self.story_similarity,
            "character_similarity": self.character_similarity,
            "world_similarity": self.world_similarity,
            "theme_similarity": self.theme_similarity,
            "power_system_similarity": self.power_system_similarity,
            "emotional_similarity": self.emotional_similarity,
            "art_style_similarity": self.art_style_similarity,
            "music_similarity": self.music_similarity,
            "pacing_similarity": self.pacing_similarity,
            "tone_similarity": self.tone_similarity,
            "audience_similarity": self.audience_similarity,
            "genre_blend_similarity": self.genre_blend_similarity,
            "overall_explanation": self.overall_explanation,
            "confidence_score": self.confidence_score,
            "connection_type": self.connection_type,
            "similarity_percentage": self.similarity_percentage,
            "rating": self.confidence_score,
            "episodes": self.episodes,
            "status": self.status,
            "type": self.anime_type,
            "url": self.url,
            "mal_id": self.mal_id,
        }


@dataclass
class RequestStats:
    total: int = 0
    active: int = 0
    completed: int = 0
    failed: int = 0
    retried: int = 0
    parallel_workers: int = 0
    queue_size: int = 0
    speed: float = 0.0
    timeline: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "active": self.active,
            "completed": self.completed,
            "failed": self.failed,
            "retried": self.retried,
            "parallel_workers": self.parallel_workers,
            "queue_size": self.queue_size,
            "speed": round(self.speed, 1),
        }


@dataclass
class PipelineState:
    task_id: str
    query: str
    user_description: str = ""
    stage: PipelineStage = PipelineStage.INIT
    progress: float = 0.0
    message: str = "Initializing..."
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0

    agents: dict[str, AgentStatus] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    discussion: list[DiscussionMessage] = field(default_factory=list)
    results: list[RankedResult] = field(default_factory=list)
    stats: RequestStats = field(default_factory=RequestStats)
    commentary: list[str] = field(default_factory=list)
    error: str | None = None

    def __post_init__(self) -> None:
        if self.created_at <= 0:
            self.created_at = time.time()
        if self.started_at <= 0:
            self.started_at = time.time()

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def elapsed(self) -> float:
        if self.completed_at > 0:
            return self.completed_at - self.started_at
        return time.time() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        agents_snapshot = {k: v.to_dict() for k, v in list(self.agents.items())}
        return {
            "task_id": self.task_id,
            "query": self.query,
            "stage": self.stage.value,
            "progress": self.progress,
            "message": self.message,
            "elapsed": round(self.elapsed(), 1),
            "agents": agents_snapshot,
            "candidate_count": len(self.candidates),
            "discussion_count": len(self.discussion),
            "result_count": len(self.results),
            "stats": self.stats.to_dict(),
            "commentary": list(self.commentary)[-30:],
            "error": self.error,
        }

    def to_sse(self) -> dict[str, Any]:
        agents_snapshot = {k: v.to_dict() for k, v in list(self.agents.items())}
        return {
            "status": self.stage.value,
            "progress": self.progress,
            "message": self.message,
            "elapsed": round(self.elapsed(), 1),
            "agents": agents_snapshot,
            "candidate_count": len(self.candidates),
            "discussion": [m.to_dict() for m in list(self.discussion)[-20:]],
            "results": [r.to_dict() for r in list(self.results)[:50]],
            "stats": self.stats.to_dict(),
            "commentary": list(self.commentary)[-20:],
            "error": self.error,
        }


_tasks: dict[str, PipelineState] = {}
_tasks_lock = threading.Lock()


def create_task(task_id: str, query: str, user_description: str = "") -> PipelineState:
    state = PipelineState(
        task_id=task_id,
        query=query,
        user_description=user_description,
    )
    with _tasks_lock:
        _tasks[task_id] = state
    return state


def get_task(task_id: str) -> PipelineState | None:
    with _tasks_lock:
        return _tasks.get(task_id)


def update_task(task_id: str, **kwargs: Any) -> None:
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(**kwargs)


def remove_task(task_id: str) -> None:
    with _tasks_lock:
        _tasks.pop(task_id, None)


def cleanup_old_tasks(max_age: float = 600.0) -> None:
    now = time.time()
    with _tasks_lock:
        expired = [k for k, v in _tasks.items() if now - v.created_at > max_age]
        for k in expired:
            del _tasks[k]
