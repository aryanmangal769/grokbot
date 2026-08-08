"""Read API for the insights tab.

Thin FastAPI layer over the Polymarket bet DB (SQLite). User interests are not
stored here — they come live from the Grok interest classifier (see
`insights.interests`) and are used to rank events per user.

Endpoints:
  GET /users                    -> demo users classified by Grok (+ their categories)
  GET /events?user=<id>&limit=  -> bet events ranked by that user's interests
  GET /events/{event_id}        -> one event + sentiment + top posts

Run (from repo root):
  uvicorn insights.api:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from insights.db import as_list, connect
from insights.interests import list_users, load_user

app = FastAPI(title="X Prediction Markets — Insights API")

# The browser mockup is served from a different localhost port; allow it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _event_row(row) -> dict:
    return {
        "id": row["id"],
        "question": row["question"],
        "category": row["category"],
        "tags": as_list(row["tags"]),
        "market_pct": round((row["yes_price"] or 0) * 100, 1),
        "yes_price": row["yes_price"],
        "no_price": row["no_price"],
        "volume": row["volume"],
        "liquidity": row["liquidity"],
        "volume_24hr": row["volume_24hr"],
        "resolution_date": row["resolution_date"],
        "polymarket_slug": row["polymarket_slug"],
        "image_url": row["image_url"],
        "sentiment": {
            "x_implied_pct": row["x_implied_pct"],
            "direction": row["direction"],
            "confidence": row["confidence"],
            "momentum_score": row["momentum_score"],
            "post_count": row["post_count"],
            "summary": row["summary"],
            "source": row["source"],
        },
    }


EVENT_SELECT = """
    SELECT e.*, s.x_implied_pct, s.direction, s.confidence, s.momentum_score,
           s.post_count, s.summary, s.source
    FROM events e LEFT JOIN sentiment s ON s.event_id = e.id
"""


@app.get("/users")
def users() -> list[dict]:
    """Demo users classified live by the Grok interest classifier."""
    return list_users()


@app.get("/events")
def events(user: str | None = None, limit: int = 6) -> list[dict]:
    categories: list[str] = []
    if user:
        u = load_user(user)
        if u is None:
            raise HTTPException(404, f"no classified interests for user '{user}'")
        categories = u["categories"]

    with connect() as conn:
        rows = conn.execute(EVENT_SELECT + " ORDER BY e.volume_24hr DESC").fetchall()

    result = [_event_row(r) for r in rows]
    if categories:
        pref = [e for e in result if e["category"] in categories]
        rest = [e for e in result if e["category"] not in categories]
        result = pref + rest  # interest-matched first, then fill
    return result[:limit]


@app.get("/events/{event_id}")
def event_detail(event_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            EVENT_SELECT + " WHERE e.id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"unknown event '{event_id}'")
        posts = conn.execute(
            """SELECT platform, author, handle, text, likes, reposts, url, stance
               FROM top_posts WHERE event_id = ?
               ORDER BY likes DESC LIMIT 20""",
            (event_id,),
        ).fetchall()

    detail = _event_row(row)
    detail["top_posts"] = [dict(p) for p in posts]
    return detail
