from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx

CONFIG_FILE = Path(".cache/config.json")
APIKEY_FILE = Path("apikey.conf")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("1", "true", "yes")


def _parse_conf(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    return result


def _write_conf(path: Path, data: dict[str, str]) -> None:
    lines = [
        "# API Keys Configuration",
        "# Do not commit this file to git.",
        "",
    ]
    for key, value in data.items():
        lines.append(f"{key}={value}")
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


APIKEY_FIELDS = {"openrouter_api_key", "local_ai_api_key"}

ENV_TO_CONF = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "local_ai_api_key": "LOCAL_AI_API_KEY",
}

CONF_TO_ENV = {v: k for k, v in ENV_TO_CONF.items()}


@dataclass(frozen=False)
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

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api"
    openrouter_model: str = "google/gemma-4-31b-it:free"

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
        if self.ai_provider == "openrouter":
            return self.openrouter_base_url
        return self.local_ai_base_url

    @property
    def effective_ai_model(self) -> str:
        if self.ai_model:
            return self.ai_model
        if self.ai_provider == "openrouter":
            return self.openrouter_model
        return self.local_ai_model

    @property
    def effective_ai_api_key(self) -> str:
        if self.ai_api_key:
            return self.ai_api_key
        if self.ai_provider == "openrouter":
            return self.openrouter_api_key
        return self.local_ai_api_key

    @property
    def is_openrouter(self) -> bool:
        return self.ai_provider == "openrouter"

    @property
    def openrouter_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        api_key = self.effective_ai_api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if self.is_openrouter:
            headers["HTTP-Referer"] = "https://anime-search.local"
            headers["X-Title"] = "AI Anime Search"
        return headers

    def validate_ai_provider(self) -> str | None:
        if self.ai_provider == "openrouter":
            if not self.openrouter_api_key and not self.ai_api_key:
                return "OpenRouter API key is required. Set it in Settings."
        if self.ai_provider == "local":
            if not self.local_ai_base_url:
                return "Local AI base URL is required."
        return None

    def to_dict(self) -> dict:
        d: dict = {}
        for k, v in self.__dict__.items():
            if k == "cache_path":
                d[k] = str(v)
            else:
                d[k] = v
        return d

    def to_public_dict(self) -> dict:
        d = self.to_dict()
        for field in APIKEY_FIELDS:
            val = d.get(field, "")
            if val:
                d[field] = val[:8] + "..." + val[-4:] if len(val) > 16 else "***"
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {}
        for k, v in data.items():
            if k in valid_fields:
                if k == "cache_path":
                    filtered[k] = Path(v) if isinstance(v, str) else v
                else:
                    filtered[k] = v
        return cls(**filtered)


def load_api_keys(settings: Settings) -> None:
    conf = _parse_conf(APIKEY_FILE)

    for field_name, conf_key in ENV_TO_CONF.items():
        env_val = os.getenv(conf_key, "")
        conf_val = conf.get(conf_key, "")
        value = env_val or conf_val
        if value:
            setattr(settings, field_name, value)


def save_api_keys(settings: Settings) -> None:
    conf = _parse_conf(APIKEY_FILE)
    for field_name, conf_key in ENV_TO_CONF.items():
        val = getattr(settings, field_name, "")
        if val:
            conf[conf_key] = val
        elif conf_key in conf:
            del conf[conf_key]
    _write_conf(APIKEY_FILE, conf)


def load_settings() -> Settings:
    s = Settings()

    s.ai_provider = _env("AI_PROVIDER", s.ai_provider)
    s.ai_api_key = _env("AI_API_KEY", s.ai_api_key)
    s.ai_base_url = _env("AI_BASE_URL", s.ai_base_url)
    s.ai_model = _env("AI_MODEL", s.ai_model)
    s.ai_timeout_seconds = _env_float("AI_TIMEOUT_SECONDS", s.ai_timeout_seconds)
    s.ai_max_tokens = _env_int("AI_MAX_TOKENS", s.ai_max_tokens)
    s.ai_temperature = _env_float("AI_TEMPERATURE", s.ai_temperature)

    s.local_ai_base_url = _env("LOCAL_AI_BASE_URL", s.local_ai_base_url)
    s.local_ai_model = _env("LOCAL_AI_MODEL", s.local_ai_model)

    s.openrouter_base_url = _env("OPENROUTER_BASE_URL", s.openrouter_base_url)
    s.openrouter_model = _env("OPENROUTER_MODEL", s.openrouter_model)

    s.agent_max_iterations = _env_int("AGENT_MAX_ITERATIONS", s.agent_max_iterations)
    s.agent_max_tool_calls = _env_int("AGENT_MAX_TOOL_CALLS", s.agent_max_tool_calls)
    s.agent_tool_delay = _env_float("AGENT_TOOL_DELAY", s.agent_tool_delay)

    s.web_host = _env("ANIME_SEARCH_WEB_HOST", s.web_host)
    s.web_port = _env_int("ANIME_SEARCH_WEB_PORT", s.web_port)
    s.web_debug = _env_bool("ANIME_SEARCH_WEB_DEBUG", s.web_debug)

    s.default_content_filter = _env("DEFAULT_CONTENT_FILTER", s.default_content_filter)

    if CONFIG_FILE.exists():
        try:
            import json
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            s = s.from_dict(saved)
        except Exception:
            pass

    load_api_keys(s)
    return s


def save_settings(settings: Settings) -> None:
    import json
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    d = settings.to_dict()
    for field in APIKEY_FIELDS:
        d.pop(field, None)

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)

    save_api_keys(settings)
