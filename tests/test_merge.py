from anime_search.merge import merge_profiles
from anime_search.models import SourceResult


def test_merge_deduplicates_titles_and_characters() -> None:
    results = [
        SourceResult(
            source="anilist",
            confidence=1.0,
            query="test",
            ok=True,
            normalized={
                "titles": {"all": ["Cowboy Bebop", "COWBOY BEBOP"]},
                "description": {"summary": "short"},
                "genres": ["Action", "Sci-Fi"],
                "characters": [{"name": "Spike Spiegel", "role": "MAIN"}],
                "media": {"poster": "large.jpg"},
            },
        ),
        SourceResult(
            source="jikan",
            confidence=0.98,
            query="test",
            ok=True,
            normalized={
                "titles": {"all": ["Cowboy Bebop"]},
                "description": {"summary": "A much longer description for merge preference."},
                "genres": ["action", "Drama"],
                "characters": [{"name": "Spike Spiegel", "image": "spike.jpg"}],
                "media": {"poster": "small.jpg", "trailer": "trailer"},
            },
        ),
    ]

    profile = merge_profiles("test", results)

    assert profile.titles["all"] == ["Cowboy Bebop"]
    assert profile.genres == ["Action", "Sci-Fi", "Drama"]
    assert len(profile.characters) == 1
    assert profile.characters[0]["image"] == "spike.jpg"
    assert profile.media["poster"] == "large.jpg"
    assert profile.media["trailer"] == "trailer"
