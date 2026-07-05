from __future__ import annotations

import re
from dataclasses import dataclass

FILLER_WORDS = {
    "anime", "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "best", "good", "great", "nice", "awesome", "amazing", "wonderful",
    "funny", "interesting", "cool", "nice", "some", "any", "all",
    "want", "need", "like", "looking", "search", "find", "watch",
    "recommend", "suggest", "show", "give", "tell", "about",
    "really", "very", "super", "most", "more", "than",
}

INTENT_PATTERNS: list[tuple[str, str]] = [
    (r"\b(?:recommend|suggestion|suggest|suggesting)\b", "recommend"),
    (r"\b(?:similar\s+to|like\s+as|reminds?\s+me)\b", "similar_to"),
    (r"\b(?:what\s+is|tell\s+me\s+about|info(?:rmation)?\s+about)\b", "info"),
    (r"\b(?:compare|versus|vs\.?|or)\b", "compare"),
    (r"\b(?:best|top|greatest|highest|most\s+popular)\b", "top_list"),
    (r"\b(?:opening|ending|theme|insert|song|ost)\b", "music"),
    (r"\b(?:character|protagonist|hero|heroine)\b", "character"),
    (r"\b(?:studio|animation\s+studio|made\s+by)\b", "studio"),
    (r"\b(?:season|episodes?|how\s+many)\b", "episodes"),
]

GENRE_SYNONYMS: dict[str, list[str]] = {
    "comedy": ["funny", "hilarious", "humorous", "lighthearted", "slapstick", "parody", "sitcom"],
    "romance": ["romantic", "love", "dating", "relationship", "shoujo"],
    "action": ["fighting", "battle", "combat", "shounen"],
    "drama": ["emotional", "serious", "deep", "thought-provoking"],
    "horror": ["scary", "terrifying", "creepy", "spooky", "horror"],
    "thriller": ["suspense", "tense", "gripping", "edge-of-seat"],
    "fantasy": ["magical", "magic", "supernatural", "isekai"],
    "sci-fi": ["science fiction", "space", "futuristic", "cyberpunk", "mecha"],
    "slice of life": ["daily life", "everyday", "realistic", "cozy", "relaxing"],
    "mystery": ["detective", "investigation", "puzzle", "whodunit"],
    "sports": ["athletic", "competition", "tournament", "team"],
    "mecha": ["robot", "giant robot", "mobile suit"],
    "isekai": ["another world", "transported", "reincarnated", "summoned"],
    "vampire": ["vampires", "undead", "blood"],
    "psychological": ["mind games", "mental", "philosophical"],
    "military": ["army", "war", "soldier", "military"],
    "historical": ["period", "ancient", "historical", "samurai", "feudal"],
    "music": ["musical", "band", "idol", "performance"],
    "school": ["high school", "college", "academy", "student"],
    "harem": ["multiple love interests", "reverse harem"],
    "ecchi": ["fanservice", "ecchi"],
}


@dataclass
class NormalizedQuery:
    original: str
    clean_query: str
    keywords: list[str]
    genres: list[str]
    intent: str
    is_description: bool


def normalize_query(raw: str, description: str = "") -> NormalizedQuery:
    original = raw.strip()
    combined = f"{original} {description}".lower()

    intent = "recommend"
    for pattern, label in INTENT_PATTERNS:
        if re.search(pattern, combined):
            intent = label
            break

    detected_genres: list[str] = []
    for genre, synonyms in GENRE_SYNONYMS.items():
        for syn in synonyms:
            if re.search(r"\b" + re.escape(syn) + r"\b", combined):
                if genre not in detected_genres:
                    detected_genres.append(genre)
                break

    is_desc = bool(description and not original)
    if not is_desc:
        word_count = len(original.split())
        if word_count > 5 or any(c in original for c in "?!"):
            is_desc = True

    clean = original.lower()
    for genre, synonyms in GENRE_SYNONYMS.items():
        clean = re.sub(r"\b" + re.escape(genre) + r"\b", "", clean)
        for syn in synonyms:
            clean = re.sub(r"\b" + re.escape(syn) + r"\b", "", clean)

    for word in FILLER_WORDS:
        clean = re.sub(r"\b" + re.escape(word) + r"\b", "", clean)

    clean = re.sub(r"[^a-z0-9\s\-]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()

    keywords = [w for w in clean.split() if len(w) > 1]

    if detected_genres:
        for g in detected_genres:
            if g not in keywords:
                keywords.insert(0, g)

    if not clean and detected_genres:
        clean = " ".join(detected_genres)

    if not clean and original:
        words = original.lower().split()
        keywords = [w for w in words if w not in FILLER_WORDS and len(w) > 1]
        clean = " ".join(keywords)

    return NormalizedQuery(
        original=original,
        clean_query=clean or original.lower(),
        keywords=keywords,
        genres=detected_genres,
        intent=intent,
        is_description=is_desc,
    )


def normalize_for_api(raw: str, api: str = "default") -> str:
    q = raw.strip().lower()
    q = re.sub(r"\s+", " ", q).strip()

    typo_map = {
        r"\baniem\b": "anime", r"\banme\b": "anime", r"\bromnce\b": "romance",
        r"\bschoo\b": "school", r"\bschol\b": "school", r"\bteh\b": "the",
        r"\bstudnt\b": "student", r"\bstuden\b": "student",
    }
    for pat, rep in typo_map.items():
        q = re.sub(pat, rep, q)

    stop_phrases = [
        (r"\babout\s+teacher\s+and\s+(?:her|his)\s+student\b", "teacher student"),
        (r"\babout\s+teacher\b", "teacher"),
        (r"\banime\s+like\b", ""), (r"\banime\s+similar\s+to\b", ""),
        (r"\bsimilar\s+to\b", ""), (r"\blike\b", ""), (r"\bwatch\b", ""),
        (r"\brecommend\b", ""), (r"\bfind\b", ""), (r"\bsearch\b", ""),
        (r"\blook\s+for\b", ""), (r"\babout\b", ""),
    ]
    for pat, rep in stop_phrases:
        q = re.sub(pat, rep, q)

    q = re.sub(r"[^a-z0-9\s\-:]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()

    if api == "anilist":
        if not q.endswith(" anime") and not any(w in q for w in ["anime", "manga", "ova", "movie", "series"]):
            q = f"{q} anime"
    elif api == "jikan":
        pass

    return q
