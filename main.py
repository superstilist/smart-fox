from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx
from flask import Flask, Response, jsonify, render_template, request

from anime_search.config import load_settings, save_settings
from anime_search.engine import AnimeSearchEngine, _get_task, cleanup_old_tasks

log = logging.getLogger(__name__)


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


async def fetch_poster_batch(
    titles: list[str],
    timeout: float = 8.0,
    delay: float = 0.4,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    semaphore = asyncio.Semaphore(3)

    async def _fetch_one(client: httpx.AsyncClient, title: str) -> None:
        async with semaphore:
            try:
                response = await client.get(
                    "https://api.jikan.moe/v4/anime",
                    params={"q": title, "limit": 1, "sfw": "true"},
                )
                if response.status_code == 429:
                    await asyncio.sleep(1.0)
                    response = await client.get(
                        "https://api.jikan.moe/v4/anime",
                        params={"q": title, "limit": 1, "sfw": "true"},
                    )
                response.raise_for_status()
                data = response.json()
                anime = (data.get("data") or [{}])[0]
                images = anime.get("images", {}).get("jpg", {}) | anime.get("images", {}).get("webp", {})
                results[title] = {
                    "poster": images.get("large_image_url") or images.get("image_url"),
                    "score": anime.get("score"),
                    "episodes": anime.get("episodes"),
                    "synopsis": (anime.get("synopsis") or "")[:300],
                    "genres": [g.get("name") for g in anime.get("genres", []) if g.get("name")][:6],
                    "type": anime.get("type"),
                    "source": anime.get("source"),
                }
            except Exception:
                results[title] = {"poster": None}
            await asyncio.sleep(delay)

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
        tasks = [_fetch_one(client, t) for t in titles[:50]]
        await asyncio.gather(*tasks, return_exceptions=True)
    return results


async def fetch_anime_detail(
    title: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
            response = await client.get(
                "https://api.jikan.moe/v4/anime",
                params={"q": title, "limit": 1, "sfw": "true"},
            )
            if response.status_code == 429:
                await asyncio.sleep(1.0)
                response = await client.get(
                    "https://api.jikan.moe/v4/anime",
                    params={"q": title, "limit": 1, "sfw": "true"},
                )
            response.raise_for_status()
            data = response.json()
            anime = (data.get("data") or [{}])[0]
            if not anime:
                return {"error": "Anime not found"}
            images = anime.get("images", {}).get("jpg", {}) | anime.get("images", {}).get("webp", {})
            genres = [g.get("name") for g in anime.get("genres", []) if g.get("name")]
            themes = [t.get("name") for t in anime.get("themes", []) if t.get("name")]
            return {
                "title": anime.get("title"),
                "title_japanese": anime.get("title_japanese"),
                "title_english": anime.get("title_english"),
                "synopsis": anime.get("synopsis"),
                "background": anime.get("background"),
                "poster": images.get("large_image_url") or images.get("image_url"),
                "banner": anime.get("images", {}).get("jpg", {}).get("large_image_url"),
                "trailer": anime.get("trailer", {}).get("url"),
                "score": anime.get("score"),
                "scored_by": anime.get("scored_by"),
                "rank": anime.get("rank"),
                "popularity": anime.get("popularity"),
                "members": anime.get("members"),
                "favorites": anime.get("favorites"),
                "episodes": anime.get("episodes"),
                "status": anime.get("status"),
                "aired": anime.get("aired", {}).get("string"),
                "rating": anime.get("rating"),
                "type": anime.get("type"),
                "source": anime.get("source"),
                "duration": anime.get("duration"),
                "rating_val": anime.get("rating"),
                "genres": genres,
                "themes": themes,
                "studios": [s.get("name") for s in anime.get("studios", []) if s.get("name")],
                "producers": [p.get("name") for p in anime.get("producers", []) if p.get("name")][:5],
                "url": anime.get("url"),
                "mal_id": anime.get("mal_id"),
            }
    except Exception as e:
        return {"error": str(e)}


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="anime_search/templates",
        static_folder="anime_search/static",
    )
    settings = load_settings()
    engine = AnimeSearchEngine(settings)

    @app.get("/")
    def index() -> str:
        return render_template("index.html", query="", profile=None, error=None, recommendation=None)

    @app.get("/api/config")
    def api_config_get() -> Any:
        d = engine.settings.to_dict()
        return jsonify(d)

    @app.post("/api/config")
    def api_config_post() -> Any:
        nonlocal settings, engine
        payload = request.get_json(silent=True) or {}
        try:
            new_settings = engine.settings.from_dict(payload)
            validation_error = new_settings.validate_ai_provider()
            if validation_error:
                return jsonify({"error": validation_error}), 400
            settings = new_settings
            save_settings(settings)
            engine = AnimeSearchEngine(settings)
            return jsonify({"status": "ok", "config": engine.settings.to_dict()})
        except Exception as exc:
            log.error("Config update failed: %s", exc)
            return jsonify({"error": str(exc)}), 400

    @app.post("/search")
    def search() -> str:
        query = request.form.get("query", "").strip()
        description = request.form.get("description", "").strip()
        content_filter = request.form.get("content_filter", "sfw").strip()
        negative_prompt = request.form.get("negative_prompt", "").strip()
        if content_filter not in ("sfw", "nsfw", "all"):
            content_filter = "sfw"
        if not query and not description:
            return render_template("index.html", query=query, profile=None, error="Enter an anime title or description.", recommendation=None), 400

        if not query and description:
            query = description[:80]

        try:
            profile = run_async(engine.search(query, description, content_filter, negative_prompt))
            recommendation = run_async(engine.recommend(query, description, content_filter, negative_prompt))
            top_recommendations = (recommendation or {}).get("top_50", []) if recommendation else []
            return render_template(
                "index.html",
                query=query,
                profile=profile.model_dump(mode="json"),
                profile_json=profile.model_dump_json(indent=2),
                error=None,
                recommendation=recommendation,
                top_recommendations=top_recommendations,
                recommendation_json=json.dumps(recommendation, ensure_ascii=False, indent=2) if recommendation else None,
                description=description,
                content_filter=content_filter,
                negative_prompt=negative_prompt,
            )
        except Exception as exc:
            log.error("Search failed for '%s': %s", query, exc)
            return render_template("index.html", query=query, profile=None, error=str(exc), recommendation=None, description=description, content_filter=content_filter, negative_prompt=negative_prompt), 502

    @app.post("/api/ai/start")
    def api_ai_start() -> Any:
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query") or "").strip()
        description = str(payload.get("description") or "").strip()
        content_filter = str(payload.get("content_filter") or "sfw").strip()
        negative_prompt = str(payload.get("negative_prompt") or "").strip()
        if content_filter not in ("sfw", "nsfw", "all"):
            content_filter = "sfw"
        if not query and not description:
            return jsonify({"error": "Missing query or description."}), 400
        if not query:
            query = description[:80]
        cleanup_old_tasks()
        task_id = engine.start_background_recommend(query, description, content_filter, negative_prompt)
        return jsonify({"task_id": task_id, "status": "started"})

    @app.get("/api/ai/status/<task_id>")
    def api_ai_status(task_id: str) -> Any:
        task = _get_task(task_id)
        if task is None:
            return jsonify({"error": "Task not found."}), 404
        return jsonify({
            "task_id": task_id,
            "status": task.get("status", "unknown"),
            "progress": task.get("progress", 0),
            "message": task.get("message", ""),
            "results": task.get("results", []),
            "error": task.get("error"),
            "profile": task.get("profile"),
            "recommendation": task.get("recommendation"),
            "tool_calls": task.get("tool_calls", []),
        })

    @app.get("/api/ai/stream/<task_id>")
    def api_ai_stream(task_id: str) -> Response:
        def generate():
            last_update = 0
            while True:
                task = _get_task(task_id)
                if task is None:
                    yield f"data: {json.dumps({'error': 'Task not found'})}\n\n"
                    return
                status = task.get("status", "unknown")
                progress = task.get("progress", 0)
                message = task.get("message", "")
                results = task.get("results", [])
                raw_text = task.get("raw_text", "")
                commentary = task.get("commentary", [])
                now = time.time()
                if now - last_update >= 0.2 or status in ("done", "error"):
                    payload = {
                        "status": status,
                        "progress": progress,
                        "message": message,
                        "count": len(results),
                    }
                    if results:
                        payload["latest"] = results[-1]
                    if raw_text:
                        payload["raw_length"] = len(raw_text)
                    if commentary:
                        payload["commentary"] = commentary[-20:]
                    yield f"data: {json.dumps(payload)}\n\n"
                    last_update = now
                if status in ("done", "error"):
                    return
                time.sleep(0.2)

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/anime/detail")
    def api_anime_detail() -> Any:
        title = request.args.get("title", "").strip()
        if not title:
            return jsonify({"error": "Missing title parameter."}), 400
        try:
            detail = run_async(fetch_anime_detail(title))
            return jsonify(detail)
        except Exception as exc:
            log.error("Anime detail fetch failed: %s", exc)
            return jsonify({"error": str(exc)}), 502

    @app.post("/api/recommend/posters")
    def api_recommend_posters() -> Any:
        payload = request.get_json(silent=True) or {}
        titles = payload.get("titles") or []
        if not isinstance(titles, list) or not titles:
            return jsonify({"error": "Missing titles array."}), 400
        try:
            results = run_async(fetch_poster_batch([str(t) for t in titles[:50]]))
            return jsonify(results)
        except Exception as exc:
            log.error("Poster fetch failed: %s", exc)
            return jsonify({}), 200

    @app.get("/api/health")
    def api_health() -> Any:
        return jsonify({
            "status": "ok",
            "ai_provider": engine.settings.ai_provider,
            "ai_model": engine.settings.effective_ai_model,
            "ai_base_url": engine.settings.effective_ai_base_url,
            "lm_studio_url": engine.settings.local_ai_base_url,
            "model": engine.settings.local_ai_model,
        })

    @app.post("/api/test-connection")
    def api_test_connection() -> Any:
        import asyncio as _aio
        async def _test():
            url = engine.settings.effective_ai_base_url.rstrip("/") + "/v1/chat/completions"
            headers = engine.settings.openrouter_headers
            payload = {
                "model": engine.settings.effective_ai_model,
                "messages": [{"role": "user", "content": "Say hello in one word."}],
                "max_tokens": 10,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                return resp.status_code, resp.text[:1000]
        try:
            status, body = _aio.run(_test())
            return jsonify({"status": status, "body": body})
        except Exception as exc:
            return jsonify({"status": 0, "error": str(exc)}), 500

    @app.get("/api/tools")
    def api_tools() -> Any:
        from anime_search.tools import TOOL_DEFINITIONS
        return jsonify({"tools": TOOL_DEFINITIONS})

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    settings = load_settings()
    host = settings.web_host
    port = settings.web_port
    debug = settings.web_debug
    app.run(host=host, port=port, debug=debug, use_reloader=debug)


if __name__ == "__main__":
    main()
