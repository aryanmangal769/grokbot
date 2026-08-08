# Handoff — X Prediction Markets Insights Tab

**Goal:** a tab inside X that shows Polymarket bets tailored to a user's interests,
with an X/Reddit sentiment read (our "X-implied %") next to the market price, and a
click-through detail view with a mock "Buy with X money" button.

**Read `docs/system-design.md` first** — this file is the current status + the
concrete TODO to make it work end to end.

---

## ✅ What's done (working today)

### Backend — `insights/` package
- **`db.py`** — SQLite bet DB (stand-in for Supabase). Tables: `events`,
  `sentiment` (1:1 with events), `top_posts`. **No persona/interest tables** —
  the DB holds only Polymarket bet data by design.
- **`seed_events.py`** — live Gamma fetch → top ~18 binary Yes/No events across
  categories (word-boundary classifier). Writes `events` + **placeholder**
  `sentiment` rows flagged `source='seed-placeholder'` (deterministic stand-in,
  NOT real analysis).
- **`interests.py`** — loads a user's **real Grok** interest classification from
  `outputs/<user>_interests.json` and maps fine-grained topics → the event
  category taxonomy, ranked by summed confidence. No DB persistence.
- **`api.py`** — FastAPI, CORS on:
  - `GET /users` — demo users classified by Grok (+ derived categories, summary, topics)
  - `GET /events?user=<id>&limit=` — events ranked by that user's interests
  - `GET /events/{event_id}` — one event + sentiment + `top_posts` (posts empty until step 5)

### Interests — real, via existing repo scripts (all Grok)
Three demo users classified from their **actual X timelines**
(`user_interest_classifier` → `outputs/*_interests.json`, gitignored):
- `@VitalikButerin` → crypto, technology, science
- `@BarackObama` → politics, sports, pop-culture
- `@business` (Bloomberg) → economics, politics, technology

### Frontend — `web/x-mockup.html`
Faithful desktop x.com feed (dark, 3-column). Right-rail **"Markets for you"**
widget reads **live from the API**: "Viewing as" user selector, top-6
interest-ranked events, each with **dual Market% vs X-implied% + momentum arrow
+ "edge" chip**, volume/liquidity, X-lean + post counts.

### Env / secrets (`.env`, gitignored)
`XAI_API_KEY`, `X_BEARER_TOKEN`, `X_API_KEY`, `X_API_SECRET` are all set and
validated. A fresh clone must recreate `.env` from `.env.example`.

---

## ▶️ How to run what exists

```bash
cd ~/Downloads/grokbot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then fill XAI_API_KEY + X_* creds

# 1. classify demo users (real Grok; needs X_BEARER_TOKEN + XAI_API_KEY)
python -m user_interest_classifier.classify VitalikButerin --max-pages 2
python -m user_interest_classifier.classify BarackObama    --max-pages 2
python -m user_interest_classifier.classify business       --max-pages 2

# 2. seed the bet DB (live Gamma fetch)
python -m insights.seed_events

# 3. serve API + frontend (two terminals)
uvicorn insights.api:app --port 8000
python3 -m http.server 8747 --directory web   # open http://localhost:8747/x-mockup.html
```

---

## 🚧 What the next agent must do (to reach true end-to-end)

### 1. STEP 5 — Real Grok Sentiment Agent ✅ DONE
`insights/build_sentiment.py`, **per event**: harvests X (`x_search.sweep_once`)
+ Reddit (PullPush keyless), reasons with Grok (structured JSON), writes real
`sentiment` (`source='grok'`) + grounded stance-tagged `top_posts`. Run:
`python -m insights.build_sentiment --limit 6` (or `--all`). ~2 min/event
(x_search sweep dominates). Degrades gracefully if x_search is empty (Reddit-only).

### 2. STEP 4 — Detail view on click
`web/x-mockup.html`: clicking a market row (they carry `data-id`) opens a panel/
modal that fetches `GET /events/{id}` and shows: top tweets/Reddit posts (with
stance), the sentiment `summary`, analytics (market vs X-implied, volume/liquidity,
momentum), and a **mock** "Buy with X money" button (confirm flow, no real funds).

### 3. Data quality — tighten the event seeder
Gamma's top-by-24h-volume currently surfaces esoteric markets (Ethiopian PM,
lower-league football). Filter/curate `seed_events.py` toward recognizable,
high-liquidity markets (Fed, Bitcoin, elections, major sports) for a compelling demo.

### 4. Orchestration
Wire fetch → search → sentiment → write as one runnable pass (and later a cron),
so the DB refreshes on an interval per `docs/system-design.md`.

### 5. (Optional / later)
- Swap SQLite → Supabase (schema is already Supabase-shaped) if a cloud DB is needed
  (e.g. real phone device reading remotely). User currently has no Supabase project.
- `outputs/*_interests.json` are gitignored → a fresh clone must re-run the
  classifier. Consider committing sample fixtures if graders need it without X creds.
- Keep browser + Apollo's Expo phone app reading the same API shapes.

---

## Interface contract (so step 5 and the UI stay in sync)

`sentiment` row / `event.sentiment` JSON:
```
x_implied_pct : float 0..100      # our estimate from X+Reddit
direction     : "up" | "down" | "flat"
momentum_score: float (signed)
confidence    : float 0..1
post_count    : str  ("2.4K")
summary       : str
source        : "grok" | "seed-placeholder"
```
`top_posts` row:
```
event_id, platform("x"|"reddit"), author, handle, text,
likes:int, reposts:int, url, stance("yes"|"no"|"neutral")
```
