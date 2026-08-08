---
name: reddit-intel
description: >
  Autonomous Reddit research for any topic or plain-English question (e.g.
  "will infantino remain fifa president"). In one call it (1) reduces the
  question to its key search terms, (2) discovers the most relevant subreddits
  Reddit-wide, (3) collects the chatter from every available source — Arctic
  Shift + PullPush archives (no key) and the official Reddit API if configured —
  and (4) returns a structured intelligence read: sentiment, competing
  narratives, the most informative posts, notable accounts, a coordination hint,
  and a prediction-market read (Grok 4.5 when XAI_API_KEY is set, else an offline
  heuristic). Every run is bounded by configurable guardrails (max subreddits,
  posts, per-sub limit, lookback window) so cost and archive load stay capped.
  Topic-agnostic and read-only. Feeds Arbiter's market-integrity engine.
implementation: backend/skills/reddit_intel.py
version: 0.2
---

# Reddit Intel skill

One call in, a full intelligence report out. **Topic-agnostic** — works for
politics, crypto, sports, tech, markets, a company, a person, any event. No
Reddit account required.

Pass a topic **or a full yes/no question** — it extracts the search terms itself:

```python
from backend.skills.reddit_intel import run
report = run("will infantino remain fifa president")   # -> searches "infantino fifa"
report = run("will bitcoin hit 150k this year")        # -> "bitcoin"
report = run("brazil presidential election")           # -> "brazil election"

# tighten the guardrails for a cheaper/faster run:
report = run("nvidia earnings", limits={"max_subreddits": 5, "max_posts": 60})
```

> Full endpoint catalog (Reddit official, Arctic Shift, PullPush, X API v2):
> see **[REDDIT_X_API_REFERENCE.md](REDDIT_X_API_REFERENCE.md)**.

## What it does (3 stages, autonomous)

1. **Discover** — `discover_subreddits(topic)`
   Ranks subreddits by real engagement on the topic (Arctic-primary over a
   candidate pool + PullPush bonus). Filters bots (`autotldr`), karma farms, and
   nation-sim/fiction subs (`worldpowers`, `AlternateHistory`, …).

2. **Collect** — `collect(topic, subs)`  ← **all sources, merged + deduped**
   | Source | Auth | Role |
   |---|---|---|
   | **Arctic Shift** | none | subreddit-scoped search, 2005→2026, timestamps |
   | **PullPush** | none | full-text across all of Reddit (best-effort) |
   | **Official Reddit API** | OAuth (optional) | live + author account-age, when creds set |
   Returns `(posts, sources_used)` so you can see which fired.

3. **Analyze** — `analyze(topic, posts)`
   Grok 4.5 reads the raw posts and returns structured insight. Falls back to an
   offline lexical heuristic when no `XAI_API_KEY` is set (so it always returns
   *something*).

## Output shape (`run` → dict)

```jsonc
{
  "topic": "brazil presidential election",
  "query_used": "brazil election",           // narrowed for archive recall
  "sources_used": ["arctic-shift", "pullpush"],
  "discovered_subreddits": [ {"subreddit":"worldnews","posts":4,"upvotes":30726}, ... ],
  "subs_monitored": ["worldnews","brasil","neoliberal", ...],
  "stats": { "posts": 17, "range": ["2026-02-19T18:07","2026-07-30T15:10"] },
  "posts_sample": [ {"source":"reddit","author":"...","ts":"...","text":"...","reach":16471}, ... ],
  "intel": {
    "sentiment": "mixed", "sentiment_score": 0.2, "conviction": 0.4,
    "momentum": "rising",
    "narratives": [ {"claim":"...","stance":"yes|no|neutral","support":123} ],
    "key_posts": ["@user: ..."],
    "notable_accounts": ["..."],
    "coordination_hint": 0.1,
    "market_read": "what this implies for the related prediction market",
    "summary": "3-4 sentences",
    "engine": "grok-4.5"            // or "offline-heuristic"
  }
}
```

## Guardrails (bounded every run)

Defaults in `DEFAULT_LIMITS`; override any subset via `run(topic, limits={...})`
or the API query params. They cap Grok token cost, result noise, and load on the
free archives — and are **not** topic-specific.

| Limit | Default | Caps |
|---|---|---|
| `max_subreddits` | 8 | subs monitored |
| `per_sub_limit` | 40 | posts pulled per subreddit (Arctic max 100) |
| `max_posts` | 150 | hard cap on posts fed to the analyzer |
| `lookback_days` | 180 | time window |
| `pool_probe` | 24 | candidate subs touched in the Arctic fallback |

The run echoes the applied `limits` and `sources_used` back in its output.

## Invoke it three ways

- **CLI:** `python -m backend.skills.reddit_intel "brazil presidential election"`
- **HTTP:** `GET /api/reddit/intel?topic=brazil%20presidential%20election&days=180`
- **Grok tool:** import `REDDIT_INTEL_TOOL` (function-calling spec) so the agent
  calls the skill on its own when a question needs crowd sentiment.

## Config

| Env | Effect |
|---|---|
| *(none)* | archives only — works out of the box |
| `XAI_API_KEY` | analysis upgrades from heuristic → Grok 4.5 |
| `REDDIT_CLIENT_ID` / `_SECRET` | adds the official API source (+ author account-age) |

## Notes / gotchas
- Archives match on narrow queries — `_core_query()` reduces a topic to its 1–2
  most distinctive tokens (3-word phrases under-recall on Arctic).
- The keyless archives **rate-limit under heavy use**; for production reliability
  use the official API (rate budget) or cache. Archives are ideal for demos.
- Author account-age (coordination/sockpuppet signal) is only available via the
  official API — the archives don't return it.
