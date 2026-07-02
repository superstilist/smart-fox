# AI Anime Search

Production-quality AI-powered anime discovery engine. Searches multiple databases simultaneously, merges data intelligently, and uses an AI agent with 12 tools to find similar anime.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, Flask, httpx, Pydantic |
| Frontend | Vanilla JS, CSS, HTML |
| AI | LM Studio (OpenAI-compatible), Function Calling |
| Databases | AniList (GraphQL), Jikan (REST), Kitsu (JSON API) |
| Cache | SQLite (WAL mode) |
| Streaming | Server-Sent Events (SSE) |

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
anime-search-web
```

Open http://127.0.0.1:5000

## How It Works

### Search Pipeline

```
User Input (title/description)
        ↓
┌─────────────────────────────────┐
│  Fetch from 3 providers in parallel  │
│  AniList + Jikan + Kitsu            │
└─────────────────────────────────┘
        ↓
┌─────────────────────────────────┐
│  Merge into UnifiedAnimeProfile     │
│  (dedup titles, combine data)       │
└─────────────────────────────────┘
        ↓
┌─────────────────────────────────┐
│  AI Agent + 12 Tools               │
│  (searches for similar anime)       │
└─────────────────────────────────┘
        ↓
┌─────────────────────────────────┐
│  Normalize & Return min 3 recs      │
└─────────────────────────────────┘
        ↓
   Display Results
```

### AI Agent with Tools

The AI agent has 12 tools to manually search anime databases:

| Tool | Description |
|------|-------------|
| `search_anime_by_title` | Find anime by name |
| `search_anime_by_genre` | Find by genre (Action, Romance, etc.) |
| `search_anime_by_studio` | Find by studio (ufotable, MAPPA, etc.) |
| `search_anime_by_theme` | Find by theme (Isekai, School, etc.) |
| `search_anime_by_keyword` | Free-text keyword search |
| `get_anime_recommendations` | Get MAL recommendations |
| `get_top_rated_anime` | Get top anime lists |
| `get_seasonal_anime` | Get seasonal anime |
| `search_anime_schedule` | Get weekly schedule |
| `get_anime_details` | Get full anime details |
| `compare_anime` | Compare two anime |
| `find_similar_by_character` | Find similar characters |

### Agent Loop

1. Send user query + profile data to AI
2. AI decides which tools to call
3. Execute tools via Jikan API
4. Feed results back to AI
5. AI analyzes and calls more tools
6. After 1-8 iterations, AI returns final JSON with recommendations

### Profile Merging

When searching "Cowboy Bebop", the engine fetches from all 3 providers:

```
AniList: { title: "Cowboy Bebop", genres: ["Action", "Drama"] }
Jikan:   { title: "Cowboy Bebop", genres: ["Action", "Drama"] }
Kitsu:   { title: "Cowboy Bebop", genres: ["Action", "Sci-Fi"] }
                    ↓
         UnifiedAnimeProfile:
         - titles: { all: ["Cowboy Bebop"] }
         - genres: ["Action", "Drama", "Sci-Fi"]  (merged, deduplicated)
         - characters: [...]  (combined from all sources)
         - recommendations: [...]  (combined from all sources)
```

### Caching

| Namespace | TTL | Description |
|-----------|-----|-------------|
| `merged` | 24h | Merged profiles |
| `anilist` | 24h | AniList responses |
| `jikan` | 24h | Jikan responses |
| `kitsu` | 24h | Kitsu responses |
| Images | Permanent | Poster images |

**Description mode** = zero caching (always fresh).

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Web GUI |
| `POST` | `/api/ai/start` | Start search → returns `{task_id}` |
| `GET` | `/api/ai/status/<task_id>` | Get task progress |
| `GET` | `/api/ai/stream/<task_id>` | SSE stream |
| `POST` | `/api/search` | Search (returns profile) |
| `POST` | `/api/recommend` | Get recommendations (sync) |
| `GET` | `/api/anime/detail?title=X` | Get full anime details |
| `POST` | `/api/recommend/posters` | Batch fetch posters |
| `GET` | `/api/health` | Health check |

## Configuration

Environment variables:

```powershell
# AI Backend
$env:LOCAL_AI_BASE_URL="http://127.0.0.1:1234"
$env:LOCAL_AI_API_KEY="local-key"
$env:LOCAL_AI_MODEL="google/gemma-4-e2b"

# Cache
$env:ANIME_SEARCH_CACHE=".cache/anime_search.sqlite3"
$env:ANIME_SEARCH_CACHE_TTL="86400"

# Timeouts
$env:ANIME_SEARCH_TIMEOUT="15"
$env:LOCAL_AI_TIMEOUT="120"

# Agent
$env:AGENT_MAX_ITERATIONS="8"
$env:AGENT_MAX_TOOLCalls="12"

# Web Server
$env:ANIME_SEARCH_WEB_HOST="127.0.0.1"
$env:ANIME_SEARCH_WEB_PORT="5000"
```

## Project Structure

```
anime_search/
├── __init__.py
├── agent.py          # AI agent with tool-calling loop
├── ai.py             # LM Studio communication, prompts
├── cache.py          # SQLite JSON cache (versioned)
├── cli.py            # Command-line interface
├── config.py         # Central configuration
├── engine.py         # Search pipeline, background tasks
├── image_cache.py    # Poster image caching
├── merge.py          # Profile merging logic
├── models.py         # Pydantic models
├── providers/        # AniList, Jikan, Kitsu
│   ├── base.py       # Base provider class
│   ├── anilist.py    # AniList GraphQL
│   ├── jikan.py      # Jikan REST
│   └── kitsu.py      # Kitsu JSON API
├── recommender.py    # Ranking, normalization, fallback
├── static/
│   ├── app.css       # Styles
│   └── app.js        # Frontend JavaScript
├── templates/
│   └── index.html    # Flask template
├── tools.py          # 12 tool definitions + executors
└── web.py            # Flask routes and API
tests/
├── test_merge.py
└── test_recommender.py
```

## Running

```powershell
# Install
pip install -e .[dev]

# Web GUI
anime-search-web

# Tests
python -m pytest tests/ -v
```

## Tests

4 tests covering:
- Merge deduplication
- Fallback recommendations shape
- AI recommendation normalization
- Fallback to top_25

```powershell
python -m pytest tests/ -v
```
