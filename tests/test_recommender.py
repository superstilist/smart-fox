from anime_search.merge import merge_profiles
from anime_search.models import SourceResult
from anime_search.recommender import fallback_recommendations, normalize_ai_recommendations


def sample_profile():
    return merge_profiles(
        "Cowboy Bebop",
        [
            SourceResult(
                source="anilist",
                confidence=1.0,
                query="Cowboy Bebop",
                ok=True,
                normalized={
                    "titles": {"all": ["Cowboy Bebop"]},
                    "description": {"summary": "Bounty hunters drift through space."},
                    "genres": ["Action", "Sci-Fi"],
                    "themes": ["Adult Cast", "Space", "Crime"],
                    "studios": ["Sunrise"],
                    "characters": [{"name": "Spike Spiegel", "role": "MAIN"}],
                    "recommendations": [
                        {"title": "Samurai Champloo", "source": "anilist", "score": 90},
                        {"title": "Trigun", "source": "anilist", "score": 82},
                    ],
                },
            ),
            SourceResult(
                source="jikan",
                confidence=0.98,
                query="Cowboy Bebop",
                ok=True,
                normalized={
                    "titles": {"all": ["Cowboy Bebop"]},
                    "genres": ["Drama"],
                    "recommendations": [
                        {"title": "Samurai Champloo", "source": "jikan"},
                        {"title": "Outlaw Star", "source": "jikan"},
                    ],
                },
            ),
            SourceResult(
                source="kitsu",
                confidence=0.95,
                query="Cowboy Bebop",
                ok=True,
                normalized={
                    "titles": {"all": ["Cowboy Bebop"]},
                    "recommendations": [{"title": "Space Dandy", "source": "kitsu"}],
                },
            ),
        ],
    )


def test_fallback_recommendations_return_ranked_top_50_shape() -> None:
    result = fallback_recommendations(sample_profile())

    assert result["engine"] == "provider-similarity-fallback"
    assert result["source_title"] == "Cowboy Bebop"
    assert len(result["top_50"]) >= 3
    first = result["top_50"][0]
    assert first["title"] == "Samurai Champloo"
    assert first["similarity_score_0_1178"] <= 1178
    assert "overall_explanation" in first
    assert "rating" in first
    assert "synopsis" in first
    assert "connection_type" in first
    assert "match_reason" in first
    assert "weighted_score" in first


def test_recommendations_have_varied_scores() -> None:
    result = fallback_recommendations(sample_profile())
    scores = [r["similarity_score_0_1178"] for r in result["top_50"]]
    assert len(set(scores)) > 1, "Recommendations should have varied scores, not all identical"


def test_recommendations_have_per_candidate_dimensions() -> None:
    result = fallback_recommendations(sample_profile())
    weighted_scores = [r["weighted_score"] for r in result["top_50"]]
    assert len(weighted_scores) >= 2
    assert len(set(weighted_scores)) > 1, "Each candidate should have different weighted scores"


def test_normalize_ai_recommendations_extracts_expected_fields() -> None:
    result = normalize_ai_recommendations(
        {
            "top_50": [
                {
                    "title": "Samurai Champloo",
                    "similarity_score_0_1178": 1000,
                    "overall_explanation": "Shared tone.",
                    "confidence_score": 91,
                    "connection_type": "studio",
                }
            ]
        },
        sample_profile(),
    )

    assert result["engine"] == "local-ai"
    assert result["top_50"][0]["similarity_percentage"] == round(1000 / 1178 * 100, 1)
    assert result["top_50"][0]["confidence_score"] == 91
    assert "rating" in result["top_50"][0]
    assert result["top_50"][0]["connection_type"] == "studio"


def test_normalize_ai_recommendations_fallback_to_top_25() -> None:
    result = normalize_ai_recommendations(
        {
            "top_25": [
                {
                    "title": "Trigun",
                    "similarity_score_0_1178": 900,
                    "overall_explanation": "Space western vibes.",
                    "confidence_score": 85,
                }
            ]
        },
        sample_profile(),
    )

    assert result["engine"] == "local-ai"
    assert result["top_50"][0]["title"] == "Trigun"
    assert result["top_50"][0]["similarity_percentage"] == round(900 / 1178 * 100, 1)
