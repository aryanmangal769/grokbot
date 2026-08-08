"""Live user interests → bet categories (no DB persistence).

Interests are NOT stored in the bet DB. They come from the Grok interest
classifier (`user_interest_classifier`), which writes `outputs/<user>_interests.json`.
This module loads that real Grok output for a demo user and maps the fine-grained
topics onto the small category taxonomy the events use, so the API can rank the
Polymarket bet DB by what a user actually cares about.

Re-run the classifier to refresh a user:
  python -m user_interest_classifier.classify VitalikButerin --max-pages 2
"""

from __future__ import annotations

import json
from pathlib import Path

from common.env import REPO_ROOT

OUTPUTS = REPO_ROOT / "outputs"

# Display metadata for the demo users we classified.
PROFILES = {
    "vitalikbuterin": {"name": "Vitalik Buterin", "handle": "@VitalikButerin", "avatar": "🦊"},
    "barackobama":    {"name": "Barack Obama",    "handle": "@BarackObama",    "avatar": "🏛️"},
    "business":       {"name": "Bloomberg",       "handle": "@business",       "avatar": "📈"},
}

# fine-grained Grok topic keyword -> event category slug
CATEGORY_MAP = {
    "crypto": ["ethereum", "crypto", "defi", "blockchain", "bitcoin", "token",
               "web3", "stablecoin", "onchain", "l2", "rollup"],
    "technology": ["ai", "artificial intelligence", "tech", "software", "formal",
                   "lean", "verification", "identity", "open model", "compute",
                   "machine", "cryptography", "privacy", "security"],
    "politics": ["politic", "democracy", "civic", "policy", "election",
                 "endorsement", "government", "campaign", "legislat"],
    "economics": ["market", "finance", "macro", "monetary", "economy",
                  "economic", "m&a", "deal", "commodit", "energy", "trade",
                  "inflation", "fed"],
    "sports": ["basketball", "sport", "football", "nba", "soccer", "tennis"],
    "science": ["climate", "environment", "science", "space", "animal", "health"],
    "world": ["geopolitic", "war", "international", "foreign", "global"],
    "pop-culture": ["music", "book", "reading", "movie", "film", "culture",
                    "celebrity", "art"],
}


def _slug_for(topic: str) -> str | None:
    t = topic.lower()
    for slug, words in CATEGORY_MAP.items():
        if any(w in t for w in words):
            return slug
    return None


def _load_raw(user_id: str) -> dict | None:
    """Find the classifier output for a user id (case-insensitive stem)."""
    for f in OUTPUTS.glob("*_interests.json"):
        if f.name.lower().startswith(user_id.lower() + "_"):
            return json.loads(f.read_text())
    return None


def load_user(user_id: str) -> dict | None:
    """Return {id, name, handle, avatar, summary, categories[], topics[]} or None.

    `categories` are the event-join slugs ranked by summed Grok confidence;
    `topics` are the raw Grok interests (topic + confidence) for display.
    """
    raw = _load_raw(user_id)
    if raw is None:
        return None
    cls = raw.get("classification") or {}
    topics = cls.get("interests") or []

    scores: dict[str, float] = {}
    for it in topics:
        slug = _slug_for(it.get("topic", ""))
        if slug:
            scores[slug] = scores.get(slug, 0.0) + float(it.get("confidence") or 0)
    categories = [s for s, _ in sorted(scores.items(), key=lambda kv: -kv[1])]

    uid = (raw.get("username") or user_id).lstrip("@").lower()
    prof = PROFILES.get(uid, {"name": uid, "handle": "@" + uid, "avatar": "👤"})
    return {
        "id": uid,
        "name": prof["name"],
        "handle": prof["handle"],
        "avatar": prof["avatar"],
        "summary": cls.get("summary"),
        "categories": categories,
        "topics": [
            {"topic": it.get("topic"), "confidence": it.get("confidence")}
            for it in topics[:8]
        ],
    }


def list_users() -> list[dict]:
    """All demo users that have a classifier output on disk."""
    users = []
    for f in sorted(OUTPUTS.glob("*_interests.json")):
        uid = f.name[: -len("_interests.json")]
        u = load_user(uid)
        if u:
            users.append(u)
    return users
