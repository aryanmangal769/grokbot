"""Seed real Polymarket events into the local DB (build step 2).

One-time Gamma fetch so the UI has live-looking data before the real Grok
pipeline (step 5) is wired. Sentiment rows written here are explicitly flagged
`source='seed-placeholder'` — a deterministic stand-in derived from the market
price, NOT real X/Reddit analysis. The Sentiment Agent overwrites them later.

Usage (from repo root):
  python -m insights.seed_events              # top ~18 events across categories
  python -m insights.seed_events --limit 30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone

import requests

from insights.db import connect, init_db

GAMMA = "https://gamma-api.polymarket.com/markets"

# Priority-ordered category rules. Politics/sports/economics are checked before
# crypto/tech because their phrases are more specific; matching is whole-word
# (regex \b) so short tokens like "ai"/"eth" don't hit "strait"/"Ethiopia".
CATEGORY_KEYWORDS = [
    ("politics", ["election", "elections", "president", "presidential", "senate",
                  "congress", "governor", "trump", "biden", "democrat",
                  "republican", "prime minister", "parliament", "cabinet",
                  "impeach", "nominee", "primary"]),
    ("sports", ["nba", "nfl", "mlb", "nhl", "world cup", "super bowl",
                "champions league", "premier league", "olympics", "playoff",
                "playoffs", "f1", "grand prix", "wimbledon", "uefa", "fifa",
                "series", "finals"]),
    ("economics", ["fed", "rate", "rates", "inflation", "recession", "gdp",
                   "unemployment", "cpi", "jobs report", "interest rate"]),
    ("crypto", ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
                "dogecoin", "stablecoin", "coinbase", "binance", "xrp"]),
    ("technology", ["ai", "openai", "gpt", "grok", "google", "apple", "tesla",
                    "spacex", "nvidia", "chatgpt", "llm", "chip", "iphone"]),
    ("science", ["nasa", "vaccine", "climate", "fusion", "mars", "nobel",
                 "asteroid"]),
    ("world", ["ukraine", "russia", "china", "israel", "gaza", "war", "nato",
               "summit", "treaty", "ceasefire", "hormuz"]),
    ("pop-culture", ["oscar", "grammy", "movie", "album", "box office", "taylor",
                     "netflix", "spotify", "billboard"]),
]


def categorize(question: str, tags: list[str]) -> str:
    hay = (question + " " + " ".join(tags)).lower()
    for cat, words in CATEGORY_KEYWORDS:
        for w in words:
            if re.search(r"\b" + re.escape(w) + r"\b", hay):
                return cat
    return "other"


def _jsonparse(value, default):
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def fetch_markets(pool: int) -> list[dict]:
    """Top binary Yes/No markets by 24h volume."""
    resp = requests.get(
        GAMMA,
        params={
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
            "limit": pool,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def placeholder_sentiment(event_id: str, yes_pct: float) -> dict:
    """Deterministic stand-in until the Grok Sentiment Agent runs.

    Nudges the market price by a stable pseudo-random delta so the UI can show a
    market-vs-X divergence. Clearly labeled so it is never mistaken for analysis.
    """
    h = int(hashlib.sha256(event_id.encode()).hexdigest(), 16)
    delta = ((h % 21) - 10)  # -10..+10 points
    x_pct = max(1.0, min(99.0, yes_pct + delta))
    direction = "up" if delta > 2 else "down" if delta < -2 else "flat"
    posts_n = 500 + (h % 15000)
    post_count = f"{posts_n/1000:.1f}K" if posts_n >= 1000 else str(posts_n)
    return {
        "x_implied_pct": round(x_pct, 1),
        "direction": direction,
        "confidence": round(0.4 + (h % 40) / 100, 2),
        "momentum_score": float(delta),
        "post_count": post_count,
        "summary": "Placeholder sentiment (pre-Grok). Real X + Reddit analysis "
                   "populated by the Sentiment Agent in build step 5.",
        "source": "seed-placeholder",
    }


def seed(limit: int, pool: int) -> None:
    init_db()
    markets = fetch_markets(pool)
    now = datetime.now(timezone.utc).isoformat()

    kept: list[dict] = []
    per_cat: dict[str, int] = {}
    for m in markets:
        outcomes = _jsonparse(m.get("outcomes"), [])
        prices = _jsonparse(m.get("outcomePrices"), [])
        if [str(o).lower() for o in outcomes] != ["yes", "no"] or len(prices) < 2:
            continue
        try:
            yes_price = float(prices[0])
            no_price = float(prices[1])
        except (ValueError, TypeError):
            continue

        tags = [t.get("label", "") for t in m.get("tags", []) if isinstance(t, dict)]
        category = categorize(m.get("question", ""), tags)
        # spread categories so every persona has content: cap 4 per category
        if per_cat.get(category, 0) >= 4:
            continue
        per_cat[category] = per_cat.get(category, 0) + 1

        eid = m.get("conditionId") or m.get("slug")
        kept.append({
            "id": eid,
            "question": m.get("question", ""),
            "category": category,
            "tags": json.dumps(tags),
            "yes_price": yes_price,
            "no_price": no_price,
            "volume": float(m.get("volumeNum") or 0),
            "liquidity": float(m.get("liquidityNum") or 0),
            "volume_24hr": float(m.get("volume24hr") or 0),
            "resolution_date": m.get("endDate"),
            "polymarket_slug": m.get("slug"),
            "image_url": m.get("image"),
            "updated_at": now,
            "_sent": placeholder_sentiment(eid, yes_price * 100),
        })
        if len(kept) >= limit:
            break

    with connect() as conn:
        for e in kept:
            s = e.pop("_sent")
            conn.execute(
                """INSERT INTO events (id, question, category, tags, yes_price,
                       no_price, volume, liquidity, volume_24hr, resolution_date,
                       polymarket_slug, image_url, updated_at)
                   VALUES (:id,:question,:category,:tags,:yes_price,:no_price,
                       :volume,:liquidity,:volume_24hr,:resolution_date,
                       :polymarket_slug,:image_url,:updated_at)
                   ON CONFLICT(id) DO UPDATE SET
                       question=excluded.question, category=excluded.category,
                       tags=excluded.tags, yes_price=excluded.yes_price,
                       no_price=excluded.no_price, volume=excluded.volume,
                       liquidity=excluded.liquidity, volume_24hr=excluded.volume_24hr,
                       resolution_date=excluded.resolution_date,
                       image_url=excluded.image_url, updated_at=excluded.updated_at""",
                e,
            )
            conn.execute(
                """INSERT INTO sentiment (event_id, x_implied_pct, direction,
                       confidence, momentum_score, post_count, summary, source,
                       updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO UPDATE SET
                       x_implied_pct=excluded.x_implied_pct,
                       direction=excluded.direction, confidence=excluded.confidence,
                       momentum_score=excluded.momentum_score,
                       post_count=excluded.post_count, summary=excluded.summary,
                       source=excluded.source, updated_at=excluded.updated_at""",
                (e["id"], s["x_implied_pct"], s["direction"], s["confidence"],
                 s["momentum_score"], s["post_count"], s["summary"], s["source"], now),
            )

    dist = ", ".join(f"{k}:{v}" for k, v in sorted(per_cat.items()))
    print(f"Seeded {len(kept)} events. Categories -> {dist}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=18, help="max events to keep")
    ap.add_argument("--pool", type=int, default=200, help="markets to fetch/scan")
    args = ap.parse_args()
    seed(args.limit, args.pool)
