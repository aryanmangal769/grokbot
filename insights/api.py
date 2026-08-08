"""Read API for the insights tab (build step 1).

Thin FastAPI layer over the SQLite store. The browser feed and the Expo phone
app both read from here; swapping to Supabase later means pointing the clients at
the Supabase REST URL instead — the JSON shapes below stay the same.

Endpoints:
  GET /personas                    -> all demo personas
  GET /events?persona=<id>&limit=  -> events filtered by persona interests
  GET /events/{event_id}           -> one event + sentiment + top posts

Run (from repo root):
  uvicorn insights.api:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from insights.db import as_list, connect

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


@app.get("/personas")
def personas() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM personas ORDER BY name").fetchall()
    return [
        {"id": r["id"], "name": r["name"], "avatar": r["avatar"],
         "interests": as_list(r["interests"])}
        for r in rows
    ]


@app.get("/events")
def events(persona: str | None = None, limit: int = 6) -> list[dict]:
    with connect() as conn:
        interests: list[str] = []
        if persona:
            prow = conn.execute(
                "SELECT interests FROM personas WHERE id = ?", (persona,)
            ).fetchone()
            if prow is None:
                raise HTTPException(404, f"unknown persona '{persona}'")
            interests = as_list(prow["interests"])

        rows = conn.execute(
            EVENT_SELECT + " ORDER BY e.volume_24hr DESC"
        ).fetchall()

    result = [_event_row(r) for r in rows]
    if interests:
        pref = [e for e in result if e["category"] in interests]
        rest = [e for e in result if e["category"] not in interests]
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
