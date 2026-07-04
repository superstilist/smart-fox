from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx


@dataclass(frozen=True)
class Settings:
    cache_path: Path = Path(".cache/anime_search.sqlite3")
    cache_ttl_seconds: int = 86400
    request_timeout_seconds: float = 15.0
    connect_timeout_seconds: float = 5.0
    max_retries: int = 2
    retry_base_delay: float = 0.5

    ai_provider: str = "local"
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_timeout_seconds: float = 120.0
    ai_max_tokens: int = 4096
    ai_temperature: float = 0.15

    local_ai_base_url: str = "http://127.0.0.1:1234"
    local_ai_api_key: str = "local-key"
    local_ai_model: str = "google/gemma-4-e2b"

    agent_max_iterations: int = 10
    agent_max_tool_calls: int = 15
    agent_tool_delay: float = 0.3

    poster_fetch_concurrency: int = 3
    poster_fetch_timeout: float = 8.0

    task_cleanup_interval: int = 300
    task_max_age: int = 600

    jikan_base_url: str = "https://api.jikan.moe/v4"
    jikan_rate_limit_delay: float = 0.4
    anilist_base_url: str = "https://graphql.anilist.co"
    kitsu_base_url: str = "https://kitsu.io/api/edge"

    web_host: str = "127.0.0.1"
    web_port: int = 5000
    web_debug: bool = False

    default_content_filter: str = "sfw"

    @property
    def http_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.request_timeout_seconds, connect=self.connect_timeout_seconds)

    @property
    def ai_http_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.ai_timeout_seconds, connect=self.connect_timeout_seconds)

    @property
    def effective_ai_base_url(self) -> str:
        if self.ai_base_url:
            return self.ai_base_url
        if self.ai_provider == "local":
            return self.local_ai_base_url
        return "http://127.0.0.1:1234"

    @property
    def effective_ai_model(self) -> str:
        if self.ai_model:
            return self.ai_model
        if self.ai_provider == "local":
            return self.local_ai_model
        return "google/gemma-4-e2b"
