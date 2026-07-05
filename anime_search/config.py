from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, get_type_hints

import httpx

CONFIG_FILE = Path("settings.json")
APIKEY_CONF = Path("apikey.conf")
SESSIONS_DIR = Path(".sessions")
TOKEN_USAGE_FILE = Path(".token_usage.json")

SENSITIVE_KEYS = {"ai_api_key", "local_ai_api_key"}

CONF_MAP: dict[str, str] = {
    "OPENROUTER_API_KEY": "ai_api_key",
    "LOCAL_AI_API_KEY": "local_ai_api_key",
}

ENV_MAP: dict[str, str] = {
    "AI_PROVIDER": "ai_provider",
    "AI_API_KEY": "ai_api_key",
    "AI_BASE_URL": "ai_base_url",
    "AI_MODEL": "ai_model",
    "AI_TIMEOUT_SECONDS": "ai_timeout_seconds",
    "AI_MAX_TOKENS": "ai_max_tokens",
    "AI_TEMPERATURE": "ai_temperature",
    "LOCAL_AI_BASE_URL": "local_ai_base_url",
    "LOCAL_AI_API_KEY": "local_ai_api_key",
    "LOCAL_AI_MODEL": "local_ai_model",
    "WEB_HOST": "web_host",
    "WEB_PORT": "web_port",
    "WEB_DEBUG": "web_debug",
    "CACHE_PATH": "cache_path",
    "CONTENT_FILTER": "default_content_filter",
}


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

    openrouter_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    openrouter_fallback_models: str = "google/gemma-4-26b-a4b-it:free,meta-llama/llama-3.3-70b-instruct:free,qwen/qwen3-next-80b-a3b-instruct:free,openai/gpt-oss-120b:free"

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

    token_budget: int = 100000
    token_warning_threshold: float = 0.8

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
        if self.ai_provider == "openrouter":
            return "https://openrouter.ai/api"
        return "http://127.0.0.1:1234"

    @property
    def effective_ai_model(self) -> str:
        if self.ai_model:
            return self.ai_model
        if self.ai_provider == "local":
            return self.local_ai_model
        if self.ai_provider == "openrouter":
            return self.openrouter_model
        return self.local_ai_model

    @property
    def openrouter_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.ai_api_key:
            headers["Authorization"] = f"Bearer {self.ai_api_key}"
        headers["HTTP-Referer"] = "https://smart-fox.local"
        headers["X-Title"] = "Smart Fox"
        return headers

    @property
    def llm_headers(self) -> dict[str, str]:
        if self.ai_provider == "openrouter":
            return self.openrouter_headers
        if self.local_ai_api_key and self.local_ai_api_key != "local-key":
            return {"Authorization": f"Bearer {self.local_ai_api_key}"}
        return {}

    def to_dict(self, include_secrets: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for f in fields(self):
            if not include_secrets and f.name in SENSITIVE_KEYS:
                continue
            value = getattr(self, f.name)
            if isinstance(value, Path):
                result[f.name] = str(value)
            else:
                result[f.name] = value
        result["has_api_key"] = bool(self.ai_api_key)
        result["has_local_api_key"] = bool(self.local_ai_api_key)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        valid_names = {f.name for f in fields(cls)}
        filtered: dict[str, Any] = {}
        for key, value in data.items():
            if key not in valid_names:
                continue
            if key == "cache_path" and isinstance(value, str):
                value = Path(value)
            filtered[key] = value
        return cls(**filtered)

    def validate_ai_provider(self) -> str | None:
        if self.ai_provider == "openrouter" and not self.ai_api_key:
            return "OpenRouter requires an API key. Set AI_API_KEY or ai_api_key."
        if self.ai_provider not in ("local", "openrouter", "custom"):
            return f"Unknown AI provider: {self.ai_provider!r}. Use 'local', 'openrouter', or 'custom'."
        return None


def _parse_apikey_conf(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if key:
                    result[key] = value
    except OSError:
        pass
    return result


def _coerce_field(value: Any, target_type: type) -> Any:
    if target_type is bool:
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    if target_type is Path:
        return Path(value) if isinstance(value, str) else value
    return target_type(value)


def auto_init_session(settings: Settings) -> str:
    sessions = list_sessions()
    if sessions:
        active = get_active_session_name()
        if active and any(s["name"] == active for s in sessions):
            return active
    name = "default"
    save_session(name, settings, "Default")
    set_active_session_name(name)
    return name


def load_settings() -> Settings:
    overrides: dict[str, Any] = {}

    active_name = get_active_session_name()
    if active_name:
        session_settings = load_session(active_name)
        if session_settings is not None:
            overrides = session_settings.to_dict(include_secrets=True)

    if not overrides and CONFIG_FILE.is_file():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                overrides = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    conf = _parse_apikey_conf(APIKEY_CONF)
    hints = get_type_hints(Settings)
    field_types = {f.name: hints.get(f.name, str) for f in fields(Settings)}
    for conf_key, field_name in CONF_MAP.items():
        if conf_key in conf and field_name in field_types:
            overrides[field_name] = _coerce_field(conf[conf_key], field_types[field_name])

    import os

    for env_key, field_name in ENV_MAP.items():
        env_val = os.environ.get(env_key)
        if env_val is not None and field_name in field_types:
            overrides[field_name] = _coerce_field(env_val, field_types[field_name])

    settings = Settings.from_dict(overrides) if overrides else Settings()

    return settings


def save_settings(settings: Settings) -> None:
    data = settings.to_dict(include_secrets=False)
    data.pop("has_api_key", None)
    data.pop("has_local_api_key", None)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    active = get_active_session_name()
    if not active:
        active = "default"
        set_active_session_name(active)
    save_session(active, settings)


def list_sessions() -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    if SESSIONS_DIR.is_dir():
        for f in sorted(SESSIONS_DIR.iterdir()):
            if f.suffix == ".json":
                try:
                    with open(f, encoding="utf-8") as fh:
                        data = json.load(fh)
                    sessions.append({
                        "name": f.stem,
                        "label": data.get("_label", f.stem),
                        "modified": f.stat().st_mtime,
                    })
                except Exception:
                    pass
    return sessions


def save_session(name: str, settings: Settings, label: str = "") -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    data = settings.to_dict(include_secrets=False)
    data.pop("has_api_key", None)
    data.pop("has_local_api_key", None)
    data["_label"] = label or name
    path = SESSIONS_DIR / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def load_session(name: str) -> Settings | None:
    path = SESSIONS_DIR / f"{name}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.pop("_label", None)
        return Settings.from_dict(data)
    except Exception:
        return None


def delete_session(name: str) -> bool:
    path = SESSIONS_DIR / f"{name}.json"
    if path.is_file():
        path.unlink()
        return True
    return False


def rename_session(old_name: str, new_name: str) -> bool:
    old_path = SESSIONS_DIR / f"{old_name}.json"
    new_path = SESSIONS_DIR / f"{new_name}.json"
    if not old_path.is_file() or new_path.is_file():
        return False
    try:
        data = json.loads(old_path.read_text(encoding="utf-8"))
        data["_label"] = new_name
        new_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        old_path.unlink()
        return True
    except Exception:
        return False


def get_active_session_name() -> str:
    active = Path(".active_session")
    if active.is_file():
        try:
            return active.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def set_active_session_name(name: str) -> None:
    active = Path(".active_session")
    active.write_text(name.strip(), encoding="utf-8")


def load_token_usage() -> dict[str, Any]:
    if TOKEN_USAGE_FILE.is_file():
        try:
            with open(TOKEN_USAGE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_token_usage(data: dict[str, Any]) -> None:
    with open(TOKEN_USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def record_token_usage(session_name: str, usage: dict[str, Any]) -> None:
    data = load_token_usage()
    session = data.get(session_name, {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
    })
    session["prompt_tokens"] += usage.get("prompt_tokens", 0)
    session["completion_tokens"] += usage.get("completion_tokens", 0)
    session["total_tokens"] += usage.get("total_tokens", 0)
    session["calls"] += 1
    session["last_used"] = time.time()
    data[session_name] = session
    save_token_usage(data)


def get_session_token_usage(session_name: str) -> dict[str, Any]:
    data = load_token_usage()
    return data.get(session_name, {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "calls": 0,
        "budget": 100000,
        "budget_used_pct": 0.0,
    })
