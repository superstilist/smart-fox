from __future__ import annotations

import argparse
import asyncio
import json
import sys

from anime_search.engine import AnimeSearchEngine


async def run() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Multi-source anime search engine")
    parser.add_argument("query", help="Anime title to search for")
    parser.add_argument("--recommend", action="store_true", help="Ask the local OpenAI-compatible model for recommendations")
    args = parser.parse_args()

    engine = AnimeSearchEngine()
    if args.recommend:
        result = await engine.recommend(args.query)
    else:
        profile = await engine.search(args.query)
        result = profile.model_dump(mode="json")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
