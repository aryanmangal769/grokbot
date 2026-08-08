"""SQLite store for the X prediction-markets insights tab.

Local-first stand-in for the Supabase (Postgres) DB in `docs/system-design.md`.
Same table shapes, so swapping to Supabase later is a Writer + base-URL change,
not a schema change.

Tables:
  personas   demo users with an interests[] tag list
  events     Polymarket events (question, prices, volume, liquidity, category)
  sentiment  per-event X-implied % + momentum + summary (1:1 with events)
  top_posts  harvested X/Reddit posts backing an event's sentiment

Interests/categories/tags are stored as JSON-encoded text arrays (SQLite has no
array type), mirroring Gamma's JSON-string quirk.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS personas (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    avatar     TEXT,
    interests  TEXT NOT NULL DEFAULT '[]'   -- JSON array of category slugs
);

CREATE TABLE IF NOT EXISTS events (
    id               TEXT PRIMARY KEY,       -- polymarket conditionId / slug
    question         TEXT NOT NULL,
    category         TEXT,                    -- single slug: crypto/politics/...
    tags             TEXT NOT NULL DEFAULT '[]',
    yes_price        REAL,                    -- 0..1 implied prob (Yes)
    no_price         REAL,
    volume           REAL,
    liquidity        REAL,
    volume_24hr      REAL,
    resolution_date  TEXT,
    polymarket_slug  TEXT,
    image_url        TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS sentiment (
    event_id        TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    x_implied_pct   REAL,                     -- 0..100 our estimate from X/Reddit
    direction       TEXT,                     -- up / down / flat
    confidence      REAL,                     -- 0..1
    momentum_score  REAL,                     -- signed
    post_count      TEXT,                     -- display string, e.g. "2.4K"
    summary         TEXT,
    source          TEXT,                     -- 'grok' | 'seed-placeholder'
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS top_posts (
    id          TEXT PRIMARY KEY,
    event_id    TEXT REFERENCES events(id) ON DELETE CASCADE,
    platform    TEXT,                          -- x / reddit
    author      TEXT,
    handle      TEXT,
    text        TEXT,
    likes       INTEGER,
    reposts     INTEGER,
    url         TEXT,
    stance      TEXT,                          -- yes / no / neutral
    captured_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
CREATE INDEX IF NOT EXISTS idx_posts_event ON top_posts(event_id);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def as_list(value: str | None) -> list:
    """Decode a JSON-array text column into a Python list."""
    if not value:
        return []
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


if __name__ == "__main__":
    init_db()
    print(f"Initialized schema at {DB_PATH}")
