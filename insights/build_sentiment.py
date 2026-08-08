"""Sentiment Agent (build step 5) — real X + Reddit analysis via Grok.

For each event in the bet DB:
  1. X harvest      x_search sweep (xAI x_search tool) over a recent window
  2. Reddit harvest PullPush keyless search on the reduced topic
  3. Analyze        Grok reads both, returns x_implied_pct + momentum + summary
                    + grounded top posts (stance-tagged)
  4. Write          overwrite sentiment (source='grok') + top_posts

All agents are Grok (xAI). Reuses x_search.extract.sweep_once and the PullPush
approach from reddit_intel.

Usage (from repo root, after seed_events):
  python -m insights.build_sentiment --limit 6      # top 6 events by 24h volume
  python -m insights.build_sentiment --all
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

from api_usage_demo.grok.client import DEFAULT_TEXT_MODEL, get_client
from insights.db import connect, init_db
from x_search.extract import sweep_once

PULLPUSH = "https://api.pullpush.io/reddit/search/submission/"
UA = {"User-Agent": "grokbot-insights/0.1"}

# reduce a market question to its distinctive search tokens (from reddit_intel)
STOP = {"presidential", "the", "a", "an", "of", "in", "for", "on", "to", "vs",
        "and", "race", "who", "will", "win", "wins", "won", "2024", "2025",
        "2026", "2027", "2028", "remain", "remains", "stay", "stays", "be",
        "become", "becomes", "get", "gets", "keep", "keeps", "continue", "still",
        "next", "year", "this", "is", "are", "was", "were", "do", "does", "did",
        "can", "could", "should", "would", "may", "might", "going", "gonna",
        "as", "after", "before", "by", "above", "below", "than", "price"}


def reduce_query(question: str, max_tokens: int = 4) -> str:
    toks = re.findall(r"[A-Za-z0-9$]+", question)
    keep = [t for t in toks if t.lower() not in STOP and len(t) > 1]
    # prefer capitalized proper nouns + numbers, else fall back to first tokens
    proper = [t for t in keep if t[0].isupper() or any(c.isdigit() for c in t)]
    chosen = (proper or keep)[:max_tokens]
    return " ".join(chosen) or question


# --------------------------------------------------------------------------- #
# harvest
# --------------------------------------------------------------------------- #
def harvest_x(question: str, *, hours: int = 168) -> list[dict]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    facet = {"query": f"Recent posts, opinions and sentiment about: {question}",
             "angle": "sentiment"}
    try:
        res = sweep_once(topic=question, facet=facet, start=start, end=end,
                         model=DEFAULT_TEXT_MODEL, max_turns=6, images=False)
    except Exception as exc:  # x_search may be gated / flaky — degrade gracefully
        print(f"    x_search failed: {exc}", file=sys.stderr)
        return []
    if res.get("error"):
        print(f"    x_search: {res['error']}", file=sys.stderr)
    return res.get("posts") or []


def harvest_reddit(question: str, *, size: int = 60) -> list[dict]:
    q = reduce_query(question)
    try:
        r = requests.get(PULLPUSH, headers=UA, timeout=30, params={
            "q": q, "size": size, "sort": "desc", "sort_type": "score"})
        r.raise_for_status()
        data = r.json().get("data") or []
    except Exception as exc:
        print(f"    reddit(pullpush) failed: {exc}", file=sys.stderr)
        return []
    posts = []
    for d in data:
        posts.append({
            "author": d.get("author"),
            "subreddit": d.get("subreddit"),
            "title": d.get("title"),
            "text": (d.get("selftext") or "")[:500],
            "score": d.get("score"),
            "num_comments": d.get("num_comments"),
            "url": "https://reddit.com" + (d.get("permalink") or ""),
        })
    return posts


# --------------------------------------------------------------------------- #
# analyze (Grok, structured)
# --------------------------------------------------------------------------- #
SENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "x_implied_pct": {"type": "number", "description": "0-100 implied Yes prob from chatter"},
        "direction": {"type": "string", "enum": ["up", "down", "flat"]},
        "momentum_score": {"type": "number"},
        "confidence": {"type": "number"},
        "post_count": {"type": "string", "description": "e.g. '2.4K'"},
        "summary": {"type": "string"},
        "top_posts": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "platform": {"type": "string", "enum": ["x", "reddit"]},
                    "author": {"type": "string"},
                    "handle": {"type": "string"},
                    "text": {"type": "string"},
                    "likes": {"type": "integer"},
                    "reposts": {"type": "integer"},
                    "url": {"type": "string"},
                    "stance": {"type": "string", "enum": ["yes", "no", "neutral"]},
                },
                "required": ["platform", "author", "handle", "text", "likes",
                             "reposts", "url", "stance"],
            },
        },
    },
    "required": ["x_implied_pct", "direction", "momentum_score", "confidence",
                 "post_count", "summary", "top_posts"],
}

SYSTEM = (
    "You are a prediction-market sentiment analyst. You are given a Polymarket "
    "question, its current market-implied Yes probability, and harvested posts "
    "from X and Reddit. Read the chatter and estimate where PUBLIC SENTIMENT "
    "sits and which way it is moving.\n"
    "- x_implied_pct: your 0-100 estimate of the Yes outcome from the crowd "
    "(this can differ from the market price — that divergence is the signal).\n"
    "- direction/momentum_score: which way sentiment is trending and how hard.\n"
    "- summary: 2-3 sentences on the narrative and any market-vs-crowd divergence.\n"
    "- top_posts: pick up to 6 of the MOST informative posts. Use ONLY posts from "
    "the provided harvest; copy author/handle/text/url verbatim, set likes/reposts "
    "to the given metrics (0 if unknown), and tag each stance yes/no/neutral.\n"
    "Return ONLY the JSON object."
)


def analyze(question: str, market_pct: float, x_posts: list[dict],
            reddit_posts: list[dict]) -> dict | None:
    if not x_posts and not reddit_posts:
        return None
    user = json.dumps({
        "question": question,
        "market_implied_yes_pct": market_pct,
        "x_posts": x_posts[:40],
        "reddit_posts": reddit_posts[:40],
    }, ensure_ascii=False)[:120000]
    try:
        resp = get_client().responses.create(
            model=DEFAULT_TEXT_MODEL,
            input=[{"role": "system", "content": SYSTEM},
                   {"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": "sentiment",
                             "schema": SENT_SCHEMA, "strict": True}},
        )
        return json.loads(resp.output_text)
    except Exception as exc:
        print(f"    grok analyze failed: {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
def write_sentiment(event_id: str, s: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO sentiment (event_id, x_implied_pct, direction,
                   confidence, momentum_score, post_count, summary, source, updated_at)
               VALUES (?,?,?,?,?,?,?, 'grok', ?)
               ON CONFLICT(event_id) DO UPDATE SET
                   x_implied_pct=excluded.x_implied_pct, direction=excluded.direction,
                   confidence=excluded.confidence, momentum_score=excluded.momentum_score,
                   post_count=excluded.post_count, summary=excluded.summary,
                   source='grok', updated_at=excluded.updated_at""",
            (event_id, s["x_implied_pct"], s["direction"], s["confidence"],
             s["momentum_score"], s["post_count"], s["summary"], now))
        conn.execute("DELETE FROM top_posts WHERE event_id = ?", (event_id,))
        for i, p in enumerate(s.get("top_posts") or []):
            conn.execute(
                """INSERT INTO top_posts (id, event_id, platform, author, handle,
                       text, likes, reposts, url, stance, captured_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (f"{event_id}:{i}", event_id, p.get("platform"), p.get("author"),
                 p.get("handle"), p.get("text"), p.get("likes") or 0,
                 p.get("reposts") or 0, p.get("url"), p.get("stance"), now))


# --------------------------------------------------------------------------- #
# orchestrate
# --------------------------------------------------------------------------- #
def run(limit: int | None) -> None:
    init_db()
    with connect() as conn:
        q = "SELECT id, question, yes_price FROM events ORDER BY volume_24hr DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        events = conn.execute(q).fetchall()

    print(f"Analyzing sentiment for {len(events)} events …")
    done = 0
    for e in events:
        mkt = round((e["yes_price"] or 0) * 100, 1)
        print(f"  • {e['question'][:60]}  (market {mkt}%)")
        xp = harvest_x(e["question"])
        rp = harvest_reddit(e["question"])
        print(f"    harvested X:{len(xp)}  reddit:{len(rp)}")
        s = analyze(e["question"], mkt, xp, rp)
        if not s:
            print("    -> no sentiment written (empty harvest / analysis failed)")
            continue
        write_sentiment(e["id"], s)
        done += 1
        print(f"    -> X-implied {s['x_implied_pct']}% {s['direction']} "
              f"| {len(s.get('top_posts') or [])} posts | {s['post_count']} vol")
    print(f"\nDone. Wrote real Grok sentiment for {done}/{len(events)} events.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--limit", type=int, default=6, help="top N events by 24h volume")
    g.add_argument("--all", action="store_true", help="process all events")
    args = ap.parse_args()
    run(None if args.all else args.limit)
